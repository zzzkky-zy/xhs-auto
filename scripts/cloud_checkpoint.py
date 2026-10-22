#!/usr/bin/env python3
"""Local sensitive checkpoint and quarantined recovery; never activate an account."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

# Script and importlib entrypoints both work without importing runtime constructors.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cloud_handoff as handoff

MARKER = 'RECOVERY-QUARANTINE.json'
FORMAT = 'xhs-private-checkpoint-v1'


def fail(code):
    raise handoff.HandoffError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def unlinked(path):
    path = Path(os.path.abspath(path))
    if any(p.is_symlink() for p in (path, *path.parents)):
        fail('UNSAFE_SYMLINK')
    return path


def new_directory(path):
    path = unlinked(path)
    if path.exists():
        fail('DESTINATION_EXISTS')
    if not path.parent.is_dir():
        fail('DESTINATION_PARENT_MISSING')
    path.mkdir(mode=0o700)
    return path


def write_json(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb') as f:
        f.write(canonical(value)); f.flush(); os.fsync(f.fileno())


def files_in(root):
    root = unlinked(root)
    if not root.is_dir():
        fail('REQUIRED_DIRECTORY_MISSING')
    files = []
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in dirs:
            p = unlinked(Path(directory) / name)
            if not p.is_dir():
                fail('UNSAFE_FILE_TYPE')
        for name in names:
            files.append(handoff.safe_file(Path(directory) / name))
    return sorted(files)


def resolve(config, value):
    p = Path(value)
    return unlinked(p if p.is_absolute() else config.parent / p)


FILE_FIELDS = {'file', 'path', 'artifact', 'asset', 'video', 'humanizer_rules', 'naturalizer_rules'}


def structured_references(value):
    """Read explicit file fields, never interpret ordinary prose or URLs as paths."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FILE_FIELDS and not isinstance(item, str) and ('sha256' in value or 'asset_sha256' in value):
                fail('INVALID_HISTORICAL_FILE_PATH')
            if key in FILE_FIELDS and isinstance(item, str):
                hash_key = 'asset_sha256' if key == 'asset' else 'sha256'
                expected = value.get(hash_key)
                if hash_key in value and (not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{64}', expected)):
                    fail('INVALID_HISTORICAL_FILE_HASH')
                if item.startswith('/'):
                    if '..' in Path(item).parts:
                        fail('UNSAFE_HISTORICAL_FILE_PATH')
                    yield item, expected
                elif expected is not None:
                    fail('UNSUPPORTED_HISTORICAL_FILE_PATH')
            elif key == 'images' and isinstance(item, list):
                for path in item:
                    if isinstance(path, str) and path.startswith('/'):
                        if '..' in Path(path).parts:
                            fail('UNSAFE_HISTORICAL_FILE_PATH')
                        yield path, None
            elif key == 'confirmation_sources' and isinstance(item, dict):
                for reference in item.values():
                    if isinstance(reference, str) and reference.startswith('/'):
                        if '..' in Path(reference).parts:
                            fail('UNSAFE_HISTORICAL_FILE_PATH')
                        yield reference, None
            yield from structured_references(item)
    elif isinstance(value, list):
        for item in value:
            yield from structured_references(item)


def inventory(config, required_files=()):
    config = handoff.safe_file(config)
    checked = handoff.inspect(config, required_files)
    settings = handoff.read_json(config)
    state = resolve(config, settings['state_dir'])
    output = resolve(config, settings.get('output_dir', str(state / 'cloud-batch-output')))
    runroot = output / 'operation-runs'
    materials = {p['path']: p for p in checked['materials']}
    references = set()
    historical_hashes = {}
    unverified_history = set()
    with handoff.database(state / 'cloud-batches.sqlite3') as db:
        meta = dict(db.execute('SELECT key,value FROM cloud_batch_meta'))
        if (meta.get('account') != checked['account_id'] or meta.get('expected_user_id') != checked['expected_user_id']
                or not meta.get('state_id')):
            fail('BATCH_IDENTITY_MISMATCH')
        runs = [r[0] for r in db.execute('SELECT id FROM ops_runs')]
        if not runs:
            fail('NO_EXISTING_RUN_HISTORY')
    directories = {}
    for run in runs:
        if not isinstance(run, str) or not re.fullmatch('[a-f0-9]{32}', run):
            fail('INVALID_RUN_ID')
        root = runroot / run
        owner = handoff.read_json(root / '.owner.json')
        if owner != {'run_id': run, 'account': checked['account_id'], 'state_id': meta['state_id']}:
            fail('RUN_OWNER_MISMATCH')
        directories[run] = sorted(str(p.relative_to(root)) for p in root.rglob('*') if p.is_dir())
        for p in files_in(root):
            materials[str(p)] = handoff.fingerprint(p)
    # Inspect every JSON column, not merely the current profile, for material refs.
    for name in handoff.DATABASES:
        with handoff.database(state / name) as db:
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            for table in tables:
                quoted = '"' + table.replace('"', '""') + '"'
                for row in db.execute('SELECT * FROM ' + quoted):
                    for value in row:
                        if isinstance(value, str):
                            try:
                                parsed = json.loads(value)
                            except (ValueError, TypeError):
                                continue
                            for reference, expected in structured_references(parsed):
                                references.add(reference)
                                if expected is None:
                                    unverified_history.add(reference)
                                else:
                                    historical_hashes.setdefault(reference, set()).add(expected)
    for reference in sorted(references):
        p = unlinked(reference)
        if p.is_dir():
            # Managed run directory references are fully covered above. Other
            # directories need an explicit integration, not guessed recursion.
            if p != runroot and not any(p == runroot / r or p.is_relative_to(runroot / r) for r in runs):
                fail('UNSUPPORTED_EXTERNAL_DIRECTORY_REFERENCE')
        else:
            materials[str(p)] = handoff.fingerprint(p)
    conflicts = []
    for reference, expected_hashes in sorted(historical_hashes.items()):
        actual = materials.get(reference, {}).get('sha256')
        if expected_hashes != {actual}:
            conflicts.append({'path': reference, 'expected_sha256': sorted(expected_hashes), 'actual_sha256': actual})
    if conflicts:
        error = handoff.HandoffError('HISTORICAL_MATERIAL_HASH_CONFLICT')
        error.conflicts = conflicts  # Local callers may save this sensitive detail privately.
        raise error
    for source in materials:
        p = Path(source)
        if p == config:
            fail('CONFIG_FILE_NOT_ALLOWED_AS_MATERIAL')
        if (p.name.lower() in {'cookies.json', 'credentials.json', 'token.json', 'tokens.json', '.env'}
                or p.name.startswith('.env.') or p.suffix.lower() in {'.pem', '.key', '.p12', '.pfx'}):
            # Refuse instead of silently dropping a file and claiming completeness.
            fail('CREDENTIAL_FILE_IN_MATERIAL_SET')
    return {'inspection': checked, 'state_id': meta['state_id'], 'runs': sorted(runs),
            'run_directories': directories, 'historical_hash_conflicts': [],
            'unverified_historical_paths': sorted(unverified_history), 'materials': [materials[k] for k in sorted(materials)]}


def copy_file(source, target):
    source = handoff.safe_file(source)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(os.open(source, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as src:
        with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb') as dst:
            while block := src.read(1024 * 1024):
                dst.write(block)
            dst.flush(); os.fsync(dst.fileno())


def backup_database(source, target):
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    with handoff.database(source) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)
            if dst.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                fail('DATABASE_INTEGRITY_FAILED')


def create(config, destination, required_files=()):
    config = handoff.safe_file(config)
    before = inventory(config, required_files)
    destination = unlinked(destination)
    settings = handoff.read_json(config)
    state = resolve(config, settings['state_dir'])
    output = resolve(config, settings.get('output_dir', str(state / 'cloud-batch-output')))
    if destination.is_relative_to(state) or destination.is_relative_to(output):
        fail('DESTINATION_OVERLAPS_SOURCE')
    destination = new_directory(destination)
    write_json(destination / 'INCOMPLETE.json', {'execution_allowed': False})
    entries = []
    for name in handoff.DATABASES:
        rel = 'state/' + name
        backup_database(state / name, destination / rel)
        item = handoff.fingerprint(destination / rel)
        entries.append({'file': rel, 'sha256': item['sha256'], 'size': item['size'], 'source': str(state / name), 'kind': 'database'})
    for i, material in enumerate(before['materials']):
        rel = 'materials/' + str(i).zfill(6)
        copy_file(material['path'], destination / rel)
        item = handoff.fingerprint(destination / rel)
        if (item['sha256'], item['size']) != (material['sha256'], material['size']):
            fail('MATERIAL_CHANGED_DURING_COPY')
        entries.append({'file': rel, 'sha256': item['sha256'], 'size': item['size'], 'source': material['path'], 'kind': 'material'})
    after = inventory(config, required_files)
    if before != after:
        fail('SOURCE_CHANGED_DURING_CHECKPOINT')
    manifest = {'format': FORMAT, 'account_id': before['inspection']['account_id'],
        'expected_user_id': before['inspection']['expected_user_id'], 'state_id': before['state_id'],
        'files': entries, 'source_inventory': before, 'consistency': 'verified_unchanged_quiescent',
        'cross_database_atomic': False, 'latest_verified': False, 'execution_allowed': False,
        'may_contain_credentials': True, 'local_private_only': True,
        'coverage': 'all_three_databases_managed_runs_structured_references_and_declared_files',
        'unstructured_or_unrecorded_external_materials_proven': False}
    write_json(destination / 'checkpoint.json', manifest)
    (destination / 'INCOMPLETE.json').unlink()
    return {'status': 'private_checkpoint_created', 'checkpoint_digest': digest(manifest),
            'files': len(entries), 'integrity_verified': True, 'latest_verified': False, 'execution_allowed': False,
            'may_contain_credentials': True}


def verify(checkpoint, expected_digest=None):
    checkpoint = unlinked(checkpoint)
    manifest = handoff.read_json(checkpoint / 'checkpoint.json')
    if not isinstance(manifest, dict) or manifest.get('format') != FORMAT:
        fail('UNSUPPORTED_CHECKPOINT')
    sha = digest(manifest)
    if expected_digest is not None and sha != expected_digest:
        fail('TRUSTED_CHECKPOINT_MISMATCH')
    if manifest.get('execution_allowed') is not False or manifest.get('latest_verified') is not False:
        fail('INVALID_CHECKPOINT_AUTHORITY')
    entries = manifest.get('files')
    if not isinstance(entries, list) or not entries:
        fail('INCOMPLETE_CHECKPOINT')
    source_inventory = manifest.get('source_inventory', {})
    inspection = source_inventory.get('inspection', {})
    if (manifest.get('account_id') != inspection.get('account_id')
            or manifest.get('expected_user_id') != inspection.get('expected_user_id')
            or not manifest.get('state_id') or manifest.get('state_id') != source_inventory.get('state_id')):
        fail('CHECKPOINT_IDENTITY_MISMATCH')
    original_files = inspection.get('files', [])
    expected_sources = {p['path']: p for p in source_inventory.get('materials', [])}
    for p in original_files:
        if Path(p['path']).name in handoff.DATABASES:
            expected_sources[p['path']] = p
    if len(expected_sources) != len(entries) or {p.get('source') for p in entries} != set(expected_sources):
        fail('INCOMPLETE_CHECKPOINT_COVERAGE')
    names = set()
    for item in entries:
        name = item.get('file')
        if not isinstance(name, str) or not re.fullmatch(r'(state/cloud-(actions|batches|pacing)\.sqlite3|materials/[0-9]{6})', name) or name in names:
            fail('INVALID_CHECKPOINT_PATH')
        names.add(name)
        original = expected_sources[item['source']]
        if name.startswith('materials/') and (item.get('sha256'), item.get('size')) != (original['sha256'], original['size']):
            fail('CHECKPOINT_MATERIAL_REFERENCE_MISMATCH')
        if name.startswith('state/') and Path(item['source']).name != Path(name).name:
            fail('CHECKPOINT_DATABASE_REFERENCE_MISMATCH')
        actual = handoff.fingerprint(checkpoint / name)
        if actual['sha256'] != item.get('sha256') or actual['size'] != item.get('size'):
            fail('CHECKPOINT_FILE_MISMATCH')
    if not {'state/' + n for n in handoff.DATABASES}.issubset(names):
        fail('INCOMPLETE_CHECKPOINT')
    actual_names = {str(p.relative_to(checkpoint)) for p in files_in(checkpoint)}
    if actual_names != names | {'checkpoint.json'}:
        fail('UNLISTED_OR_INCOMPLETE_CHECKPOINT_FILES')
    for name in handoff.DATABASES:
        with handoff.database(checkpoint / 'state' / name) as db:
            kind = name.removeprefix('cloud-').removesuffix('.sqlite3')
            recorded = inspection.get('metadata', {}).get(kind, {})
            if handoff.schema_fingerprint(db) != {k: recorded.get(k) for k in ('schema_sha256', 'user_version')}:
                fail('CHECKPOINT_SCHEMA_MISMATCH')
            if kind == 'batches':
                meta = dict(db.execute('SELECT key,value FROM cloud_batch_meta'))
                if (meta.get('account'), meta.get('expected_user_id'), meta.get('state_id')) != (manifest['account_id'], manifest['expected_user_id'], manifest['state_id']):
                    fail('CHECKPOINT_IDENTITY_MISMATCH')
                if sorted(r[0] for r in db.execute('SELECT id FROM ops_runs')) != source_inventory.get('runs'):
                    fail('INCOMPLETE_CHECKPOINT_RUNS')

    return {'status': 'checkpoint_integrity_verified', 'checkpoint_digest': sha,
            'integrity_verified': True, 'trusted_digest_matched': expected_digest is not None,
            'latest_verified': False, 'execution_allowed': False, 'may_contain_credentials': True}


def restore(checkpoint, destination, expected_digest=None):
    verified = verify(checkpoint, expected_digest)
    checkpoint = unlinked(checkpoint)
    manifest = handoff.read_json(checkpoint / 'checkpoint.json')
    # Bind the exact in-memory copy plan before creating or reading any target.
    # A later verification cannot undo an unverified path already copied.
    if digest(manifest) != verified['checkpoint_digest']:
        fail('CHECKPOINT_CHANGED_BEFORE_RESTORE')
    destination = unlinked(destination)
    if destination.is_relative_to(checkpoint):
        fail('DESTINATION_OVERLAPS_SOURCE')
    destination = new_directory(destination)
    marker = {'schema': 'xhs-recovery-quarantine-v1', 'checkpoint_digest': verified['checkpoint_digest'],
        'execution_allowed': False, 'latest_verified': False, 'requires_independent_current_state_review': True}
    write_json(destination / MARKER, marker)
    evidence = destination / 'evidence'
    (evidence / 'state').mkdir(parents=True, mode=0o700)
    write_json(evidence / 'state' / MARKER, marker)
    mapping = {}
    for item in manifest['files']:
        copy_file(checkpoint / item['file'], evidence / item['file'])
        actual = handoff.fingerprint(evidence / item['file'])
        if actual['sha256'] != item['sha256']:
            fail('RESTORE_FILE_MISMATCH')
        mapping[item['source']] = str(evidence / item['file'])
    # A source modified while being copied cannot produce a successful restore.
    if verify(checkpoint, verified['checkpoint_digest'])['checkpoint_digest'] != verified['checkpoint_digest']:
        fail('CHECKPOINT_CHANGED_DURING_RESTORE')
    write_json(destination / 'source-path-map.json', mapping)
    write_json(destination / 'checkpoint.json', manifest)
    return {**verified, 'status': 'restored_to_quarantine', 'live_state_changed': False,
            'activated': False, 'immutable_history_rewritten': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('create'); p.add_argument('--config', required=True, type=Path)
    p.add_argument('--destination', required=True, type=Path)
    p.add_argument('--required-file', action='append', default=[], type=Path)
    for command in ('verify', 'restore'):
        p = sub.add_parser(command); p.add_argument('--checkpoint', required=True, type=Path)
        p.add_argument('--expected-checkpoint-digest')
        if command == 'restore': p.add_argument('--destination', required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'create': result = create(args.config, args.destination, args.required_file)
        elif args.command == 'verify': result = verify(args.checkpoint, args.expected_checkpoint_digest)
        else: result = restore(args.checkpoint, args.destination, args.expected_checkpoint_digest)
        print(json.dumps(result)); return 0
    except Exception as error:
        print(json.dumps({'status': 'blocked', 'code': str(error) if isinstance(error, handoff.HandoffError) else 'INPUT_OR_IO_ERROR',
                          'execution_allowed': False, 'latest_verified': False})); return 2


if __name__ == '__main__':
    sys.exit(main())
