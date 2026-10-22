#!/usr/bin/env python3
"""Inspect existing quiescent cloud snapshots. Never initialize, migrate or restore."""
import argparse
import collections
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
DATABASES = ('cloud-actions.sqlite3', 'cloud-batches.sqlite3', 'cloud-pacing.sqlite3')


class HandoffError(ValueError):
    pass


def fail(code):
    raise HandoffError(code)


def safe_file(path):
    path = Path(os.path.abspath(path))
    for component in (path, *path.parents):
        if component.is_symlink():
            fail('UNSAFE_SYMLINK')
    try:
        info = path.stat()
    except FileNotFoundError:
        fail('REQUIRED_FILE_MISSING')
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        fail('UNSAFE_FILE_TYPE')
    return path


def fingerprint(path):
    path = safe_file(path)
    before = path.stat()
    h = hashlib.sha256()
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            h.update(chunk)
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        fail('SNAPSHOT_CHANGED_DURING_READ')
    return {'path': str(path), 'size': before.st_size, 'sha256': h.hexdigest()}


def read_json(path):
    path = safe_file(path)
    if path.stat().st_size > 16 * 1024 * 1024:
        fail('JSON_TOO_LARGE')
    try:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'r') as source:
            return json.load(source)
    except (ValueError, UnicodeError):
        fail('INVALID_JSON')


@contextmanager
def database(path):
    path = safe_file(path)
    for suffix in ('-wal', '-journal', '-shm'):
        sidecar = Path(str(path) + suffix)
        if sidecar.is_symlink() or (sidecar.exists() and sidecar.stat().st_size):
            fail('SNAPSHOT_NOT_QUIESCENT')
    db = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            fail('DATABASE_INTEGRITY_FAILED')
        yield db
    finally:
        db.close()


def highwater(db, table, key):
    row = db.execute('SELECT COUNT(*), COALESCE(MAX(' + key + '),0) FROM ' + table).fetchone()
    return {'count': row[0], 'max_id': row[1]}


def schema_fingerprint(db):
    schema = [list(row) for row in db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]
    return {'schema_sha256': hashlib.sha256(json.dumps(schema, separators=(',', ':')).encode()).hexdigest(),
            'user_version': db.execute('PRAGMA user_version').fetchone()[0]}


def code_fingerprint(root):
    root = Path(root)
    paths = [root / 'VERSION', root / 'plugin.json', root / 'scripts/cloud_handoff.py']
    for pattern in ('runtime/*.py', 'scripts/*.py', 'schemas/*.json', 'skills/*/SKILL.md'):
        found = sorted(root.glob(pattern))
        if not found:
            fail('CODE_FINGERPRINT_UNAVAILABLE')
        paths.extend(found)
    return {'source_files': {str(p.relative_to(root)): fingerprint(p)['sha256'] for p in sorted(set(paths))}}


def inspect(config, required_files=(), code_root=ROOT):
    config = safe_file(config)
    config_before = fingerprint(config)
    settings = read_json(config)
    if not isinstance(settings, dict):
        fail('INVALID_CONFIG_OBJECT')
    if settings.get('browser_mode') != 'cloud_host':
        fail('CLOUD_CONFIG_REQUIRED')
    account, expected = settings.get('account_id'), settings.get('expected_user_id')
    if not all(isinstance(x, str) and x.strip() for x in (account, expected)):
        fail('EXACT_ACCOUNT_REQUIRED')
    state = Path(settings['state_dir'])
    if not state.is_absolute():
        state = config.parent / state
    paths = [config, *(state / name for name in DATABASES)]
    originals = {str(safe_file(p)): fingerprint(p) for p in paths}
    if originals[str(config)] != config_before:
        fail('SNAPSHOT_CHANGED_DURING_READ')
    metadata, assets = {}, {}
    def asset(path, digest=None):
        item = fingerprint(path)
        if digest is not None and item['sha256'] != digest:
            fail('MATERIAL_HASH_MISMATCH')
        assets[item['path']] = item
    try:
        with database(state / DATABASES[0]) as db:
            rows = db.execute('SELECT account,operation_id,status,binding_json FROM actions WHERE account=?', (account,)).fetchall()
            if not rows:
                fail('NO_EXISTING_ACCOUNT_HISTORY')
            statuses = collections.Counter()
            unknown = []
            for row in rows:
                binding = json.loads(row['binding_json'])
                if binding.get('expected_account') != expected or binding.get('ledger_account') != account:
                    fail('ACCOUNT_BINDING_MISMATCH')
                statuses[row['status']] += 1
                if row['status'] in {'submitted_unknown', 'dispatching'}:
                    unknown.append(row['operation_id'])
                for item in binding.get('media_manifest', []):
                    asset(item['path'], item['sha256'])
            metadata['actions'] = {**schema_fingerprint(db), 'events': highwater(db, 'action_events', 'id'),
                                   'statuses': dict(sorted(statuses.items())), 'unknown_ids': sorted(unknown)}
        with database(state / DATABASES[1]) as db:
            if not db.execute('SELECT 1 FROM tasks WHERE account=? LIMIT 1', (account,)).fetchone():
                fail('NO_EXISTING_BATCH_HISTORY')
            metadata['batches'] = {**schema_fingerprint(db), 'content_stage_highwater': highwater(db, 'ops_content_stages', 'seq'),
                                   'runs': db.execute('SELECT COUNT(*) FROM ops_runs').fetchone()[0],
                                   'unknown_task_ids': [r[0] for r in db.execute("SELECT id FROM tasks WHERE account=? AND status IN ('submitted_unknown','dispatching') ORDER BY id", (account,))]}
            policy = db.execute('SELECT body FROM ops_policy WHERE id=1').fetchone()
            if not policy:
                fail('PROFILE_MISSING')
            version = json.loads(policy[0]).get('content_profile_version')
            profile = db.execute('SELECT body FROM ops_profile_versions WHERE version=?', (version,)).fetchone()
            if not profile:
                fail('PROFILE_MISSING')
            profile = json.loads(profile[0])
            required = list(profile.get('product_materials', []))
            if profile.get('face_reference'):
                required.append(profile['face_reference'])
            for item in required:
                asset(item['file'], item['sha256'])
        with database(state / DATABASES[2]) as db:
            if not db.execute('SELECT 1 FROM pacing_accounts WHERE account=?', (expected,)).fetchone():
                fail('PACING_ACCOUNT_MISSING')
            metadata['pacing'] = {**schema_fingerprint(db), 'events': highwater(db, 'pacing_events', 'id'),
                                  'unknown_attempts': [r[0] for r in db.execute("SELECT attempt FROM pacing_attempts WHERE account=? AND status='unknown' ORDER BY attempt", (expected,))],
                                  'unknown_steps': [r[0] for r in db.execute("SELECT step FROM pacing_steps WHERE account=? AND status IN ('unknown','in_flight') ORDER BY step", (expected,))]}
        for path in required_files:
            asset(path)
        for item in [*originals.values(), *assets.values()]:
            if fingerprint(item['path']) != item:
                fail('SNAPSHOT_CHANGED_DURING_READ')
        for name in DATABASES:
            # Reject a writer that appeared while another database was inspected.
            with database(state / name):
                pass
        return {'format': 1, 'scope': 'quiescent_same_path_snapshot_only',
                'config_path': str(config), 'account_id': account, 'expected_user_id': expected,
                'code': code_fingerprint(code_root), 'required_files': sorted(str(safe_file(p)) for p in required_files),
                'files': list(originals.values()), 'materials': list(assets.values()), 'metadata': metadata,
                'cross_database_atomic': False, 'proves_latest_external_state': False,
                'secrets_exported': False}
    except (sqlite3.Error, KeyError, TypeError, json.JSONDecodeError):
        fail('UNSUPPORTED_OR_INCOMPLETE_SCHEMA')


def write_manifest(config, output, required_files=(), code_root=ROOT):
    report = inspect(config, required_files, code_root)
    output = Path(os.path.abspath(output))
    for component in (output, *output.parents):
        if component.is_symlink():
            fail('UNSAFE_SYMLINK')
    try:
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        fail('OUTPUT_EXISTS')
    with os.fdopen(fd, 'w') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    return {'status': 'manifest_created', 'account_fingerprint': hashlib.sha256(report['expected_user_id'].encode()).hexdigest(),
            'files_checked': len(report['files']) + len(report['materials']), 'execution_allowed': False}


def check_manifest(path, code_root=ROOT):
    baseline = read_json(path)
    if not isinstance(baseline, dict):
        fail('INVALID_MANIFEST_OBJECT')
    if baseline.get('format') != 1 or baseline.get('scope') != 'quiescent_same_path_snapshot_only':
        fail('UNSUPPORTED_MANIFEST')
    current = inspect(baseline['config_path'], baseline['required_files'], code_root)
    differences = []
    for field in ('account_id', 'expected_user_id', 'files', 'materials', 'metadata'):
        if current[field] != baseline[field]:
            differences.append('SNAPSHOT_' + field.upper() + '_DIFFERS')
    if current['code'] != baseline['code']:
        differences.append('CODE_VERSION_REVIEW_REQUIRED')
    return {'status': 'matches_recorded_snapshot' if not differences else 'review_required',
            'difference_codes': differences, 'execution_allowed': False,
            'proves_latest_external_state': False, 'state_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('manifest'); create.add_argument('--config', required=True, type=Path)
    create.add_argument('--output', required=True, type=Path)
    create.add_argument('--required-file', action='append', default=[], type=Path)
    check = sub.add_parser('check'); check.add_argument('--manifest', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = write_manifest(args.config, args.output, args.required_file) if args.command == 'manifest' else check_manifest(args.manifest)
        print(json.dumps(result)); return 0 if result['status'] != 'review_required' else 2
    except Exception as error:
        # Never echo file contents, SQL payloads, OS paths, tokens or credentials.
        print(json.dumps({'status': 'blocked', 'code': str(error) if isinstance(error, HandoffError) else 'INPUT_OR_IO_ERROR', 'execution_allowed': False}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
