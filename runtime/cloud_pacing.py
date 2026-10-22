"""Durable, local-only gate immediately before an actual host browser action.

A permitted reservation is consumed once, not an execution lease. Lost responses
must be reconciled by the host and must never be replayed. Cache/local reads do
not call this gate. CloudActions remains the authority for account serialization,
permissions, claim validity and unknown outcomes. Use one private state directory
per actual platform account. Nothing here sleeps or operates a browser.
"""
import contextlib
import datetime as dt
import json
import math
import os
from pathlib import Path
import random
import sqlite3
import stat
import time

from cloud_actions import ACTION_OPERATIONS, READ_OPERATIONS, _identifier, _json, _text

READS = READ_OPERATIONS | {'list_notifications'}
WRITES = ACTION_OPERATIONS - READ_OPERATIONS
COMMENTS = frozenset({'post_comment_to_feed', 'reply_comment_in_feed', 'reply_notification'})
PUBLICATIONS = frozenset({'publish_content', 'publish_with_video'})
AUTHOR_WINDOW = 3 * 86400
STEP_KINDS = frozenset({'navigation', 'search', 'reload', 'card_read', 'click', 'scroll', 'media_request', 'read_observation'})
NETWORK_READ_STEPS = frozenset({'navigation', 'search', 'reload', 'card_read'})
INTERNAL_STEPS = frozenset({'click', 'scroll', 'media_request', 'read_observation'})


class CloudPacing:
    """Atomic per-platform-account timing, rolling quotas and author selection.

    reserve's attempt_id must be stable across local rate-limit retries. A
    consumed attempt never becomes permitted again. parent_operation_id refers
    to the main comment's pacing attempt_id. Distinct suitable reply targets
    may share that successful parent on the exact observed thread and author.
    author_evidence is a bounded host UI observation, not independent proof.
    """
    def __init__(self, config, *, clock=None, random_uniform=None):
        self.config = Path(config).resolve()
        self.clock = clock or time.time
        self.random_uniform = random_uniform or random.SystemRandom().uniform
        settings = self._settings()
        self.account = settings['expected_user_id']
        directory = Path(settings['state_dir']).expanduser()
        if not directory.is_absolute(): directory = self.config.parent / directory
        if any(p.is_symlink() for p in (directory, *directory.parents)):
            raise ValueError('refuse linked state directory')
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not directory.is_dir() or stat.S_IMODE(directory.stat().st_mode) & 0o077:
            raise ValueError('state directory must be private (0700)')
        self.directory = Path(os.path.abspath(directory))
        self.path = self.directory / 'cloud-pacing.sqlite3'
        for suffix in ('', '-journal', '-wal', '-shm'):
            p = Path(str(self.path) + suffix)
            if p.is_symlink() or (p.exists() and (not p.is_file() or stat.S_IMODE(p.stat().st_mode) & 0o077)):
                raise ValueError('pacing files must be private regular files')
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('pacing ledger must be a private regular file')
            self._file_identity = (info.st_dev, info.st_ino)
        finally: os.close(fd)
        self.db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA cache_size=-2048')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS pacing_accounts (
                account TEXT PRIMARY KEY, action_at REAL NOT NULL DEFAULT 0,
                read_at REAL NOT NULL DEFAULT 0, search_at REAL NOT NULL DEFAULT 0,
                write_at REAL NOT NULL DEFAULT 0, publication_at REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS pacing_attempts (
                account TEXT NOT NULL, attempt TEXT NOT NULL, binding TEXT NOT NULL,
                operation TEXT NOT NULL, author TEXT, thread TEXT, parent TEXT,
                status TEXT NOT NULL, retry_at REAL NOT NULL DEFAULT 0,
                dispatched_at REAL, result TEXT, PRIMARY KEY(account,attempt));
            CREATE INDEX IF NOT EXISTS pacing_window ON pacing_attempts(account,dispatched_at);
            CREATE INDEX IF NOT EXISTS pacing_author ON pacing_attempts(account,author,dispatched_at);
            CREATE TABLE IF NOT EXISTS pacing_steps (
                account TEXT NOT NULL, step TEXT NOT NULL, attempt TEXT NOT NULL,
                binding TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
                reserved_at REAL, retry_at REAL NOT NULL DEFAULT 0,
                charge_read INTEGER NOT NULL DEFAULT 0, charge_search INTEGER NOT NULL DEFAULT 0,
                uses_credit INTEGER NOT NULL DEFAULT 0, result TEXT, updated_at REAL,
                PRIMARY KEY(account,step));
            CREATE INDEX IF NOT EXISTS pacing_steps_active ON pacing_steps(account,status);
            CREATE INDEX IF NOT EXISTS pacing_steps_window ON pacing_steps(account,reserved_at);
            CREATE TABLE IF NOT EXISTS pacing_blocks (
                account TEXT NOT NULL, scope TEXT NOT NULL, blocked_at REAL NOT NULL,
                reason TEXT NOT NULL, evidence TEXT NOT NULL, active INTEGER NOT NULL,
                PRIMARY KEY(account,scope));
            CREATE TABLE IF NOT EXISTS pacing_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,
                attempt TEXT NOT NULL, at REAL NOT NULL, event TEXT NOT NULL, evidence TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS pacing_evidence_update BEFORE UPDATE ON pacing_events
                BEGIN SELECT RAISE(ABORT, 'immutable pacing evidence'); END;
            CREATE TRIGGER IF NOT EXISTS pacing_evidence_delete BEFORE DELETE ON pacing_events
                BEGIN SELECT RAISE(ABORT, 'immutable pacing evidence'); END;
        ''')
        # Additive migration preserves every previously drawn deadline.
        if 'step_at' not in {r[1] for r in self.db.execute('PRAGMA table_info(pacing_accounts)')}:
            self.db.execute('ALTER TABLE pacing_accounts ADD COLUMN step_at REAL NOT NULL DEFAULT 0')

    def close(self): self.db.close()

    def _settings(self):
        data = json.loads(self.config.read_text())
        if data.get('browser_mode') != 'cloud_host': raise ValueError('cloud_host config required')
        _text(data.get('expected_user_id'), 'expected_user_id', 256)
        _text(data.get('state_dir'), 'state_dir', 4096)
        from cloud_quarantine import assert_not_quarantined
        assert_not_quarantined(data['state_dir'], self.config.parent)
        if hasattr(self, 'account') and data['expected_user_id'] != self.account:
            raise ValueError('ERR_ACCOUNT_CHANGED: reopen pacing gate')
        if hasattr(self, 'directory'):
            directory = Path(data['state_dir']).expanduser()
            if not directory.is_absolute(): directory = self.config.parent / directory
            if Path(os.path.abspath(directory)) != self.directory:
                raise ValueError('ERR_STATE_DIRECTORY_CHANGED: reopen configured pacing ledger')
        return data

    def _check_storage(self):
        self._settings()
        if any(os.path.islink(p) for p in (self.directory, *self.directory.parents)):
            raise ValueError('refuse linked state directory')
        info = os.stat(self.directory)
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError('state directory must remain private (0700)')
        for suffix in ('', '-journal', '-wal', '-shm'):
            try: info = os.lstat(str(self.path) + suffix)
            except FileNotFoundError:
                if suffix: continue
                raise ValueError('ERR_LEDGER_REPLACED: pacing ledger missing') from None
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError('pacing ledger must remain private and unlinked')
            if not suffix and (info.st_dev, info.st_ino) != self._file_identity:
                raise ValueError('ERR_LEDGER_REPLACED: pacing ledger identity changed')

    @contextlib.contextmanager
    def _transaction(self):
        self._check_storage()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def _draw(self, settings, kind, defaults):
        fixed = settings.get(kind + '_interval_seconds')
        low = settings.get(kind + '_interval_min_seconds', fixed if fixed is not None else defaults[0])
        high = settings.get(kind + '_interval_max_seconds', fixed if fixed is not None else defaults[1])
        if any(type(x) not in (int, float) or not math.isfinite(x) for x in (low, high)) or not 0 <= low <= high <= 86400:
            raise ValueError('invalid ' + kind + ' interval')
        value = self.random_uniform(low, high)
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError('invalid random interval')
        return value

    def _event(self, attempt, now, event, evidence):
        self.db.execute('INSERT INTO pacing_events(account,attempt,at,event,evidence) VALUES(?,?,?,?,?)',
                        (self.account, attempt, now, event, _json(evidence)))

    def reserve(self, operation, attempt_id, *, target_author_id=None, thread_id=None,
                parent_operation_id=None, author_evidence=None):
        if operation not in ACTION_OPERATIONS: raise ValueError('unsupported browser operation')
        _identifier(attempt_id, 'attempt_id')
        for name, value in [('target_author_id', target_author_id), ('thread_id', thread_id), ('parent_operation_id', parent_operation_id)]:
            if value is not None: _text(value, name, 256)
        if operation in COMMENTS:
            if not target_author_id or not thread_id or not isinstance(author_evidence, dict) or not author_evidence:
                raise ValueError('comment requires observed target author, thread and UI evidence')
        elif any(x is not None for x in (target_author_id, thread_id, parent_operation_id, author_evidence)):
            raise ValueError('author context is only valid for comments and replies')
        if parent_operation_id is not None and operation not in {'reply_comment_in_feed', 'reply_notification'}:
            raise ValueError('only paired replies may name a parent comment')
        target_comment_id = None
        if parent_operation_id is not None:
            target_comment_id = author_evidence.get('target_comment_id')
            _text(target_comment_id, 'observed target_comment_id', 256)
        binding = _json({'target_comment_id': target_comment_id, 'operation': operation, 'author': target_author_id, 'thread': thread_id,
                         'parent': parent_operation_id})
        settings = self._settings()
        now = self.clock()
        if type(now) not in (int, float) or not math.isfinite(now): raise ValueError('invalid clock')
        with self._transaction():
            prior = self.db.execute('SELECT * FROM pacing_attempts WHERE account=? AND attempt=?', (self.account, attempt_id)).fetchone()
            if prior and prior['binding'] != binding: raise ValueError('ERR_PACING_BINDING_MISMATCH')
            if prior and prior['dispatched_at'] is not None:
                return {'permitted': False, 'status': 'already_reserved', 'attempt_id': attempt_id,
                        'retry_at': None, 'original_status': prior['status']}
            if not prior:
                self.db.execute('INSERT INTO pacing_attempts(account,attempt,binding,operation,author,thread,parent,status) VALUES(?,?,?,?,?,?,?,?)',
                                (self.account, attempt_id, binding, operation, target_author_id, thread_id, parent_operation_id, 'waiting'))
                self._event(attempt_id, now, 'prepared', author_evidence or {})
            unfinished = self.db.execute("SELECT step,status FROM pacing_steps WHERE account=? AND status IN ('in_flight','unknown')", (self.account,)).fetchall()
            if unfinished:
                return {'permitted':False, 'status':'unfinished_browser_step', 'retry_at':None, 'steps':[dict(r) for r in unfinished]}
            scopes = ['account'] + (['search'] if operation == 'search_feeds' else []) + (['write'] if operation in WRITES else [])
            blocks = self.db.execute('SELECT scope,blocked_at,reason,evidence FROM pacing_blocks WHERE account=? AND active=1', (self.account,)).fetchall()
            blocked = [dict(r) for r in blocks if r['scope'] in scopes]
            if blocked:
                return {'permitted': False, 'status': 'platform_blocked', 'attempt_id': attempt_id,
                        'retry_at': None, 'blocks': blocked}
            self.db.execute('INSERT OR IGNORE INTO pacing_accounts(account) VALUES(?)', (self.account,))
            account = self.db.execute('SELECT * FROM pacing_accounts WHERE account=?', (self.account,)).fetchone()
            deadlines = [account['action_at'], prior['retry_at'] if prior else 0]
            if operation in READS: deadlines.append(account['read_at'])
            if operation in WRITES: deadlines.append(account['write_at'])
            if operation == 'search_feeds': deadlines.append(account['search_at'])
            if operation in PUBLICATIONS: deadlines.append(account['publication_at'])
            recent = self.db.execute('SELECT operation,dispatched_at FROM pacing_attempts WHERE account=? AND dispatched_at>? ORDER BY dispatched_at', (self.account, now - 3600)).fetchall()
            for applicable, key, default, predicate in [
                (operation in READS, 'read_max_per_hour', 120, lambda x: x in READS),
                (operation == 'search_feeds', 'search_max_per_hour', 20, lambda x: x == 'search_feeds')]:
                limit = settings.get(key, default)
                if type(limit) is not int or limit < 1: raise ValueError('invalid ' + key)
                times = [r['dispatched_at'] for r in recent if predicate(r['operation'])]
                column = 'charge_read' if key == 'read_max_per_hour' else 'charge_search'
                times += [r[0] for r in self.db.execute('SELECT reserved_at FROM pacing_steps WHERE account=? AND reserved_at>? AND '+column+'=1', (self.account,now-3600))]
                times.sort()
                if applicable and len(times) >= limit: deadlines.append(times[-limit] + 3600)
            if operation in COMMENTS:
                authors = self.db.execute("SELECT * FROM pacing_attempts WHERE account=? AND author=? AND dispatched_at>? AND status IN ('unknown','success')", (self.account, target_author_id, now - AUTHOR_WINDOW)).fetchall()
                parent = None
                if parent_operation_id:
                    parent = self.db.execute('SELECT * FROM pacing_attempts WHERE account=? AND attempt=?', (self.account, parent_operation_id)).fetchone()
                    if not parent or parent['operation'] != 'post_comment_to_feed' or parent['status'] != 'success' or parent['author'] != target_author_id or parent['thread'] != thread_id:
                        raise ValueError('paired reply requires successful same-author same-thread main comment')
                    paired = self.db.execute('SELECT binding FROM pacing_attempts WHERE account=? AND parent=? AND dispatched_at IS NOT NULL', (self.account, parent_operation_id)).fetchall()
                    if any(json.loads(r['binding']).get('target_comment_id') == target_comment_id for r in paired):
                        raise ValueError('paired reply target already reserved; reconcile original attempt')
                conflicts = [r for r in authors if not parent or not (
                    r['attempt'] == parent_operation_id or
                    (r['parent'] == parent_operation_id and r['status'] == 'success' and r['thread'] == thread_id))]
                deadlines.extend(r['dispatched_at'] + AUTHOR_WINDOW for r in conflicts)
            retry_at = max(deadlines)
            if retry_at > now:
                self.db.execute('UPDATE pacing_attempts SET retry_at=? WHERE account=? AND attempt=?', (retry_at, self.account, attempt_id))
                return {'permitted': False, 'status': 'rate_limited', 'error': 'ERR_RATE_LIMIT',
                        'attempt_id': attempt_id, 'retry_at': retry_at}
            # Draw only after every gate passes; persist all deadlines atomically.
            action_at = now + self.random_uniform(10, 20)
            if not math.isfinite(action_at) or not now + 10 <= action_at <= now + 20: raise ValueError('invalid action interval')
            read_at = now + self._draw(settings, 'read', (10, 20)) if operation in READS else account['read_at']
            search_at = now + self._draw(settings, 'search', (60, 90)) if operation == 'search_feeds' else account['search_at']
            write_at = now + self._draw(settings, 'write', (60, 180)) if operation in WRITES else account['write_at']
            publication_at = now + 60 if operation in PUBLICATIONS else account['publication_at']
            self.db.execute('UPDATE pacing_accounts SET action_at=?,read_at=?,search_at=?,write_at=?,publication_at=? WHERE account=?', (action_at,read_at,search_at,write_at,publication_at,self.account))
            self.db.execute("UPDATE pacing_attempts SET status='unknown',dispatched_at=?,retry_at=0 WHERE account=? AND attempt=?", (now,self.account,attempt_id))
            step_delay=self._draw(settings,'browser_step',(10,20))
            step_at=max(account['step_at'],now+step_delay)
            self.db.execute('UPDATE pacing_accounts SET step_at=? WHERE account=?',(step_at,self.account))
            self._event(attempt_id, now, 'reserved_unknown', {'author_evidence':author_evidence or {},
                'draws':{'action':action_at-now,'read':read_at-now if operation in READS else None,
                    'search':search_at-now if operation=='search_feeds' else None,
                    'write':write_at-now if operation in WRITES else None,'browser_step':step_delay},
                'deadlines':{'action_at':action_at,'read_at':read_at,'search_at':search_at,
                    'write_at':write_at,'publication_at':publication_at,'step_at':step_at}})
            return {'permitted': True, 'status': 'reserved', 'attempt_id': attempt_id, 'retry_at': None,
                    'next_action_at': action_at, 'next_read_at': read_at, 'next_search_at': search_at,
                    'next_write_at': write_at, 'next_publication_at': publication_at,'next_step_at':step_at}

    def record_result(self, attempt_id, status, evidence):
        """Record observed outcome without returning execution permission.

        unknown can become success or not_applied after explicit reconciliation;
        terminal results cannot be changed. All earlier evidence stays immutable.
        """
        _identifier(attempt_id, 'attempt_id')
        aliases = {'host_reported_success':'success', 'submitted_unknown':'unknown', 'confirmed':'success'}
        status = aliases.get(status, status)
        if status not in {'success', 'unknown', 'not_applied'}: raise ValueError('invalid pacing outcome')
        if not isinstance(evidence, dict) or not evidence: raise ValueError('observed result evidence required')
        encoded = _json(evidence)
        with self._transaction():
            row = self.db.execute('SELECT * FROM pacing_attempts WHERE account=? AND attempt=?', (self.account,attempt_id)).fetchone()
            if not row or row['dispatched_at'] is None: raise ValueError('no dispatched reservation')
            if row['status'] != 'unknown' and row['status'] != status: raise ValueError('terminal pacing result cannot change')
            self.db.execute('UPDATE pacing_attempts SET status=?,result=? WHERE account=? AND attempt=?', (status,encoded,self.account,attempt_id))
            self._event(attempt_id, self.clock(), status, evidence)
        return {'attempt_id': attempt_id, 'status': status, 'permitted': False}

    def events(self, attempt_id):
        self._check_storage()
        _identifier(attempt_id, 'attempt_id')
        return [dict(r) for r in self.db.execute('SELECT at,event,evidence FROM pacing_events WHERE account=? AND attempt=? ORDER BY id', (self.account,attempt_id))]

    def report_block(self, scope, reason, evidence):
        """Persist a platform obstruction; elapsed cooldown never clears it."""
        self._settings()
        if scope not in {'search', 'write', 'account'}: raise ValueError('invalid block scope')
        if reason not in {'300013', 'captcha', 'login', 'permission'}: raise ValueError('invalid block reason')
        if not isinstance(evidence, dict) or not evidence: raise ValueError('block evidence required')
        encoded = _json(evidence)
        now = self.clock()
        with self._transaction():
            self.db.execute('INSERT INTO pacing_blocks(account,scope,blocked_at,reason,evidence,active) VALUES(?,?,?,?,?,1) ON CONFLICT(account,scope) DO UPDATE SET blocked_at=excluded.blocked_at,reason=excluded.reason,evidence=excluded.evidence,active=1', (self.account,scope,now,reason,encoded))
            self._event('block_' + scope, now, 'platform_blocked', {'reason': reason, 'evidence': evidence})
        return {'status':'platform_blocked', 'scope':scope, 'retry_at':None}

    def clear_block(self, scope, evidence):
        """Only a fresh explicit host recovery observation can clear a block.

        This records evidence, never authenticates it or authorizes a browser
        probe. Host policy must establish recovery via an approved path first.
        """
        self._settings()
        if scope not in {'search', 'write', 'account'}: raise ValueError('invalid block scope')
        if not isinstance(evidence, dict) or evidence.get('expected_account') != self.account or evidence.get('status') != 'resolved' or not evidence.get('evidence_ref'):
            raise ValueError('bound explicit recovery evidence required')
        observed = evidence.get('observed_at')
        if isinstance(observed, str):
            try:
                parsed = dt.datetime.fromisoformat(observed.replace('Z', '+00:00'))
                if parsed.tzinfo is None: raise ValueError()
                observed = parsed.timestamp()
            except ValueError: raise ValueError('timezone-aware observed_at required') from None
        now = self.clock()
        if type(observed) not in (int, float) or not math.isfinite(observed) or observed > now or now - observed > 120:
            raise ValueError('fresh recovery observation required')
        _json(evidence)
        with self._transaction():
            block = self.db.execute('SELECT * FROM pacing_blocks WHERE account=? AND scope=?', (self.account,scope)).fetchone()
            if not block or not block['active']: raise ValueError('no active block')
            if observed <= block['blocked_at']: raise ValueError('recovery evidence predates block')
            self.db.execute('UPDATE pacing_blocks SET active=0 WHERE account=? AND scope=?', (self.account,scope))
            self._event('block_' + scope, now, 'platform_block_cleared', evidence)
        return {'status':'cleared', 'scope':scope, 'permitted':False}

    def reserve_step(self, attempt_id, step_id, kind, target, *, recovery=False, recovery_evidence=None):
        """Reserve exactly one native browser call, never a script/batch permit.

        target is an inert host-verified URL or single UI target description.
        navigation/search are new-page network reads; search includes filter
        requests. media_request within the existing detail is an internal step. click/scroll include a single native UI interaction. If that
        interaction triggers a search/network read, use search/navigation instead.
        DOM snapshots/screenshots are read_observation: configured internal gap (10–20s by default),
        no network-read quota. Local JSON/cache work does not call this method.
        First matching network request uses the logical reservation's quota
        credit; later new-page requests debit quotas. Internal steps use only
        the persisted internal deadline (this account overrides it to 1–5s);
        unconfigured accounts retain 10–20s. New-page steps keep the 10–20s gap.

        recovery permits ONLY an observation after an explicitly reported unknown
        (never while any call is in flight). The host must independently authorize
        recovery and verify the prior browser call ended. It clears nothing.
        """
        _identifier(attempt_id, 'attempt_id'); _identifier(step_id, 'step_id')
        if kind not in STEP_KINDS: raise ValueError('invalid browser step kind')
        _text(target, 'single browser target', 1000)
        if type(recovery) is not bool: raise ValueError('invalid recovery flag')
        if recovery:
            if not isinstance(recovery_evidence,dict) or (kind != 'read_observation' and not (kind=='reload' and recovery_evidence.get('page_reload') is True) and not (kind=='click' and recovery_evidence.get('challenge_interaction') is True) and not (kind=='navigation' and recovery_evidence.get('session_recovery') is True) and not (kind=='navigation' and recovery_evidence.get('post_submit_readback') is True)) or recovery_evidence.get('expected_account') != self.account or not recovery_evidence.get('evidence_ref') or not recovery_evidence.get('user_source') or recovery_evidence.get('call_ended') is not True or not recovery_evidence.get('recovery_reason'):
                raise ValueError('recovery requires bound authorized read-only observation evidence')
        elif recovery_evidence is not None: raise ValueError('unexpected recovery evidence')
        binding = _json({'attempt_id':attempt_id,'kind':kind,'target':target,'recovery':recovery,'recovery_evidence':recovery_evidence})
        settings = self._settings(); now = self.clock()
        if type(now) not in (int,float) or not math.isfinite(now): raise ValueError('invalid clock')
        with self._transaction():
            attempt = self.db.execute('SELECT * FROM pacing_attempts WHERE account=? AND attempt=?',(self.account,attempt_id)).fetchone()
            if not attempt or attempt['dispatched_at'] is None: raise ValueError('step requires consumed logical reservation')
            prior = self.db.execute('SELECT * FROM pacing_steps WHERE account=? AND step=?',(self.account,step_id)).fetchone()
            if prior and prior['binding'] != binding: raise ValueError('ERR_STEP_BINDING_MISMATCH')
            if prior and prior['reserved_at'] is not None:
                return {'permitted':False,'status':'already_reserved','step_id':step_id,'retry_at':None,'original_status':prior['status']}
            if not recovery and attempt['status'] != 'unknown': raise ValueError('logical operation is already terminal')
            unfinished = self.db.execute("SELECT step,status FROM pacing_steps WHERE account=? AND status IN ('in_flight','unknown')",(self.account,)).fetchall()
            if unfinished and (not recovery or any(r['status']=='in_flight' for r in unfinished)):
                return {'permitted':False,'status':'unfinished_browser_step','retry_at':None,'steps':[dict(r) for r in unfinished]}
            scopes = {'account'} | ({'search'} if kind=='search' else set()) | ({'write'} if attempt['operation'] in WRITES else set())
            blocked = [dict(r) for r in self.db.execute('SELECT scope,blocked_at,reason,evidence FROM pacing_blocks WHERE account=? AND active=1',(self.account,)) if recovery or r['scope'] in scopes]
            post_submit_readback = recovery and recovery_evidence.get('post_submit_readback') is True
            if post_submit_readback:
                final=self.db.execute("SELECT * FROM pacing_steps WHERE account=? AND attempt=? AND step=?",(self.account,attempt_id,recovery_evidence.get('final_step_id'))).fetchone()
                if (attempt['operation']!='publish_content' or kind not in {'navigation','read_observation'}
                        or recovery_evidence.get('final_submit_invoked') is not True
                        or not final or final['kind']!='click' or final['status']!='completed'):
                    raise ValueError('ERR_PUBLICATION_READBACK: completed final click and passive/native-management read only')
                if unfinished:return {'permitted':False,'status':'unfinished_browser_step','retry_at':None,'steps':[dict(r) for r in unfinished]}
                if blocked and kind=='navigation':return {'permitted':False,'status':'platform_blocked','retry_at':None,'blocks':blocked}
            if recovery and recovery_evidence.get('page_reload') is True:
                if kind!='reload' or attempt['operation'] not in READS or unfinished:
                    raise ValueError('ERR_PAGE_RELOAD: live read page only; no unfinished calls or writes')
                if not blocked or any(block['reason']!='captcha' for block in blocked):
                    raise ValueError('ERR_PAGE_RELOAD: only actual CAPTCHA, never rate or permission bypass')
            if recovery and recovery_evidence.get('challenge_interaction') is True:
                if kind!='click' or not recovery_evidence.get('challenge_authorization_source'):
                    raise ValueError('ERR_CAPTCHA_SCOPE: explicit challenge interaction authority required')
                if unfinished:
                    return {'permitted':False,'status':'unfinished_browser_step','retry_at':None,'steps':[dict(r) for r in unfinished]}
                if not blocked or any('captcha' not in str(block['reason']).lower() for block in blocked):
                    raise ValueError('ERR_CAPTCHA_SCOPE: only an actually recorded CAPTCHA block may be handled')
            if recovery and recovery_evidence.get('session_recovery') is True:
                if (kind!='navigation' or target!='https://www.xiaohongshu.com/'
                        or recovery_evidence.get('prior_session_unavailable') is not True
                        or not recovery_evidence.get('new_browser_context')
                        or not recovery_evidence.get('session_evidence_ref')):
                    raise ValueError('ERR_SESSION_RECOVERY: observed lost session and official home only')
                if unfinished:
                    return {'permitted':False,'status':'unfinished_browser_step','retry_at':None,'steps':[dict(r) for r in unfinished]}
                if not blocked or any(block['reason'] not in {'captcha','login'} for block in blocked):
                    raise ValueError('ERR_SESSION_RECOVERY: only auth/challenge recovery, not rate or permission bypass')
            # Completed upload calls may outlive the lease while awaiting approval.
            # Permit only passive verification of that same inspection attempt.
            # This grants no click, navigation, upload replay or publication.
            completed_inspection_upload = (
                attempt['operation'] == 'inspect_publication_schedule'
                and self.db.execute(
                    "SELECT 1 FROM pacing_steps WHERE account=? AND attempt=? AND kind='media_request' AND status='completed'",
                    (self.account, attempt_id)).fetchone() is not None
            )
            denied_publication_observation = (
                recovery and attempt['operation']=='publish_content'
                and recovery_evidence.get('final_submit_invoked') is False
                and isinstance(recovery_evidence.get('denied_step_id'), str)
                and self.db.execute(
                    "SELECT 1 FROM pacing_steps WHERE account=? AND attempt=? AND step=? AND kind='click' AND status='not_applied'",
                    (self.account,attempt_id,recovery_evidence['denied_step_id'])).fetchone() is not None
            )
            retained_publication_observation = (
                recovery and kind=='read_observation' and attempt['operation']=='publish_content'
                and recovery_evidence.get('retained_editor') is True
                and recovery_evidence.get('final_submit_invoked') is False
                and not unfinished
                and self.db.execute("SELECT 1 FROM pacing_steps WHERE account=? AND attempt=? AND kind='media_request' AND status='completed'",(self.account,attempt_id)).fetchone() is not None
            )
            if recovery and not unfinished and not blocked and not completed_inspection_upload and not denied_publication_observation and not retained_publication_observation and not post_submit_readback:
                raise ValueError('recovery requires an actual unresolved step, platform block, completed inspection upload, or verified denied publication click')
            if blocked and not recovery:
                return {'permitted':False,'status':'platform_blocked','retry_at':None,'blocks':blocked}
            if not prior:
                self.db.execute("INSERT INTO pacing_steps(account,step,attempt,binding,kind,status) VALUES(?,?,?,?,?,'waiting')",(self.account,step_id,attempt_id,binding,kind))
                self._event(attempt_id,now,'step_prepared',{'step_id':step_id,'binding':json.loads(binding)})
            credit_used = self.db.execute('SELECT 1 FROM pacing_steps WHERE account=? AND attempt=? AND uses_credit=1',(self.account,attempt_id)).fetchone()
            uses_credit = bool(not credit_used and ((kind=='search' and attempt['operation']=='search_feeds') or (kind=='navigation' and attempt['operation'] in READS and attempt['operation']!='search_feeds')))
            charge_read = kind in NETWORK_READ_STEPS and not uses_credit
            charge_search = kind=='search' and not uses_credit
            account = self.db.execute('SELECT * FROM pacing_accounts WHERE account=?',(self.account,)).fetchone()
            deadlines = [account['step_at'],prior['retry_at'] if prior else 0]
            if kind not in INTERNAL_STEPS:deadlines.append(account['action_at'])
            if kind in NETWORK_READ_STEPS: deadlines.append(account['read_at'])
            if kind=='search':
                # The logical reservation already checked prior search pacing.
                # Its first actual request uses that credit, not its own newly
                # allocated next-search deadline. An intervening search from
                # another workflow must still keep the shared deadline intact.
                later_logical=self.db.execute("SELECT 1 FROM pacing_attempts WHERE account=? AND operation='search_feeds' AND attempt!=? AND dispatched_at>=? LIMIT 1",(self.account,attempt_id,attempt['dispatched_at'])).fetchone()
                later_step=self.db.execute("SELECT 1 FROM pacing_steps WHERE account=? AND kind='search' AND reserved_at>=? LIMIT 1",(self.account,attempt['dispatched_at'])).fetchone()
                if not uses_credit or later_logical or later_step:
                    deadlines.append(account['search_at'])
            for charge,key,default,column in [(charge_read,'read_max_per_hour',120,'charge_read'),(charge_search,'search_max_per_hour',20,'charge_search')]:
                limit=settings.get(key,default)
                if type(limit) is not int or limit<1: raise ValueError('invalid '+key)
                if not charge: continue
                rows=self.db.execute('SELECT operation,dispatched_at FROM pacing_attempts WHERE account=? AND dispatched_at>?',(self.account,now-3600)).fetchall()
                times=[r['dispatched_at'] for r in rows if (r['operation'] in READS if column=='charge_read' else r['operation']=='search_feeds')]
                times += [r[0] for r in self.db.execute('SELECT reserved_at FROM pacing_steps WHERE account=? AND reserved_at>? AND '+column+'=1',(self.account,now-3600))]
                times.sort()
                if len(times)>=limit: deadlines.append(times[-limit]+3600)
            retry_at=max(deadlines)
            if retry_at>now:
                self.db.execute('UPDATE pacing_steps SET retry_at=? WHERE account=? AND step=?',(retry_at,self.account,step_id))
                return {'permitted':False,'status':'rate_limited','error':'ERR_RATE_LIMIT','step_id':step_id,'retry_at':retry_at}
            action_at=account['action_at']
            if kind not in INTERNAL_STEPS:
                action_at=now+self.random_uniform(10,20)
                if not math.isfinite(action_at) or not now+10<=action_at<=now+20: raise ValueError('invalid action interval')
            step_delay=self._draw(settings,'browser_step',(10,20))
            step_at=now+step_delay
            read_at=now+self._draw(settings,'read',(10,20)) if kind in NETWORK_READ_STEPS else account['read_at']
            search_at=now+self._draw(settings,'search',(60,90)) if kind=='search' else account['search_at']
            self.db.execute('UPDATE pacing_accounts SET action_at=?,read_at=?,search_at=?,step_at=? WHERE account=?',(action_at,read_at,search_at,step_at,self.account))
            self.db.execute("UPDATE pacing_steps SET status='in_flight',reserved_at=?,retry_at=0,charge_read=?,charge_search=?,uses_credit=? WHERE account=? AND step=?",(now,int(charge_read),int(charge_search),int(uses_credit),self.account,step_id))
            self._event(attempt_id,now,'step_reserved',{'step_id':step_id,'kind':kind,'target':target,'recovery':recovery,
                'draws':{'browser_step':step_delay,'action':action_at-now if kind not in INTERNAL_STEPS else None,
                    'read':read_at-now if kind in NETWORK_READ_STEPS else None,'search':search_at-now if kind=='search' else None},
                'deadlines':{'step_at':step_at,'action_at':action_at,'read_at':read_at,'search_at':search_at}})
            return {'permitted':True,'status':'reserved','step_id':step_id,'attempt_id':attempt_id,'retry_at':None,'next_action_at':action_at,'next_step_at':step_at,'next_at':step_at if kind in INTERNAL_STEPS else max(step_at,action_at),'next_search_at':search_at,'single_native_call_only':True,'charged_read':bool(charge_read),'charged_search':bool(charge_search)}

    def complete_step(self, step_id, outcome, evidence, *, platform_restriction=None):
        """Persist actual observed outcome, including restrictions atomically.

        completed means the single call ended with its observed effect, not that
        a parent publication/comment succeeded. unknown stays blocking forever
        until explicit observed reconciliation via this same method. No outcome
        reissues permission. In-flight calls must never be silently timed out.
        """
        _identifier(step_id,'step_id')
        if outcome not in {'completed','not_applied','unknown'}: raise ValueError('invalid step outcome')
        if not isinstance(evidence,dict) or not evidence: raise ValueError('step outcome evidence required')
        encoded=_json(evidence)
        if platform_restriction is not None:
            if not isinstance(platform_restriction,dict) or set(platform_restriction)!={'scope','reason','evidence'}: raise ValueError('exact platform restriction fields required')
            if platform_restriction['scope'] not in {'search','write','account'} or platform_restriction['reason'] not in {'300013','captcha','login','permission'} or not isinstance(platform_restriction['evidence'],dict) or not platform_restriction['evidence']: raise ValueError('invalid platform restriction')
            _json(platform_restriction)
        now=self.clock()
        with self._transaction():
            row=self.db.execute('SELECT * FROM pacing_steps WHERE account=? AND step=?',(self.account,step_id)).fetchone()
            if not row or row['reserved_at'] is None: raise ValueError('no consumed browser step')
            if row['status'] in {'completed','not_applied'}:
                if row['status']!=outcome or row['result']!=encoded or platform_restriction is not None: raise ValueError('terminal browser step cannot change')
                return {'step_id':step_id,'status':outcome,'permitted':False}
            if row['status']=='unknown' and outcome!='unknown':
                observed=evidence.get('observed_at')
                if isinstance(observed,str):
                    try:
                        parsed=dt.datetime.fromisoformat(observed.replace('Z','+00:00'))
                        if parsed.tzinfo is None: raise ValueError()
                        observed=parsed.timestamp()
                    except ValueError: raise ValueError('timezone-aware reconciliation observation required') from None
                if evidence.get('expected_account')!=self.account or not evidence.get('evidence_ref') or type(observed) not in (int,float) or not math.isfinite(observed) or observed<row['updated_at'] or observed>now or now-observed>120:
                    raise ValueError('fresh account-bound actual reconciliation evidence required')
            self.db.execute('UPDATE pacing_steps SET status=?,result=?,updated_at=? WHERE account=? AND step=?',(outcome,encoded,now,self.account,step_id))
            self._event(row['attempt'],now,'step_'+outcome,{'step_id':step_id,'evidence':evidence})
            if platform_restriction is not None:
                scope=platform_restriction['scope'];reason=platform_restriction['reason'];detail=_json(platform_restriction['evidence'])
                self.db.execute('INSERT INTO pacing_blocks(account,scope,blocked_at,reason,evidence,active) VALUES(?,?,?,?,?,1) ON CONFLICT(account,scope) DO UPDATE SET blocked_at=excluded.blocked_at,reason=excluded.reason,evidence=excluded.evidence,active=1',(self.account,scope,now,reason,detail))
                self._event(row['attempt'],now,'step_platform_blocked',{'step_id':step_id,**platform_restriction})
        return {'step_id':step_id,'status':outcome,'permitted':False}

    def get_step(self, step_id):
        """Bound local step receipt; no browser action or permission."""
        self._check_storage(); _identifier(step_id,'step_id')
        row=self.db.execute('SELECT * FROM pacing_steps WHERE account=? AND step=?',(self.account,step_id)).fetchone()
        if not row: raise ValueError('browser step not found')
        value=dict(row)
        value['step_id']=value.pop('step');value['attempt_id']=value.pop('attempt')
        value['binding']=json.loads(value['binding'])
        value['result']=json.loads(value['result']) if value['result'] else None
        value['permitted']=False
        return value

    def steps_for_attempt(self, attempt_id, limit=1000):
        """Bounded ordered step receipts; truncation fails closed, never hides unknown."""
        self._check_storage();_identifier(attempt_id,'attempt_id')
        if type(limit) is not int or not 1<=limit<=1000:raise ValueError('invalid step receipt limit')
        rows=self.db.execute('SELECT step FROM pacing_steps WHERE account=? AND attempt=? ORDER BY rowid LIMIT ?',(self.account,attempt_id,limit+1)).fetchall()
        if len(rows)>limit:raise ValueError('step receipt limit exceeded; inspect bounded individual receipts')
        return [self.get_step(r['step']) for r in rows]
