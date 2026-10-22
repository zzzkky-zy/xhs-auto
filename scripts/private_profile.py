#!/usr/bin/env python3
"""Export current private business profile; prepare relocatable files without touching live state."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sqlite3

ROOT = Path(__file__).resolve().parents[1]

# Explicitly portable preferences only. Neither future configuration keys nor
# nested unknown fields become exportable merely because they exist locally.
PREFERENCE_FIELDS = {
    'schema_version', 'browser_mode', 'account_id', 'expected_user_id',
    'timeout_seconds', 'max_read_attempts', 'captcha_cooldown',
    'max_auto_recoveries', 'read_cache_seconds', 'search_interval_min_seconds',
    'search_interval_max_seconds', 'search_max_per_hour', 'search_cache_seconds',
    'detail_cache_seconds', 'read_interval_min_seconds', 'read_interval_max_seconds',
    'read_max_per_hour', 'platform_rate_limit_cooldown',
    'write_interval_min_seconds', 'write_interval_max_seconds',
}
NESTED_PREFERENCES = {
    'operations': {'timezone', 'schedule', 'activity', 'topic_template', 'is_original',
                   'content_type_declaration', 'require_group_binding', 'require_copy_disabled'},
    'retention': {'managed_disk_limit_mb', 'abandoned_keep_hours', 'read_keep_hours', 'write_detail_keep_days'},
    'resources': {'go_memory_limit_mb', 'go_max_procs', 'state_disk_limit_mb', 'min_free_disk_mb', 'min_free_inodes'},
}


def portable_preferences(settings):
    if not isinstance(settings, dict):
        raise ValueError('invalid runtime preferences')
    scalar = lambda value: value is None or type(value) in (str, int, float, bool)
    if any(not scalar(v) for k, v in settings.items() if k in PREFERENCE_FIELDS):
        raise ValueError('unsupported portable preference value')
    result = {k: v for k, v in settings.items() if k in PREFERENCE_FIELDS}
    for section, fields in NESTED_PREFERENCES.items():
        if section in settings:
            if not isinstance(settings[section], dict):
                raise ValueError('invalid nested runtime preferences')
            if any(not scalar(v) for k, v in settings[section].items() if k in fields):
                raise ValueError('unsupported nested portable preference value')
            result[section] = {k: v for k, v in settings[section].items() if k in fields}
    return result


def safe_path(path):
    path = Path(os.path.abspath(Path(path).expanduser()))
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError('symbolic links are not supported')
    return path


def read_bytes(path):
    path = safe_path(path)
    initial = path.stat()
    if not stat.S_ISREG(initial.st_mode) or initial.st_nlink != 1:
        raise ValueError('only regular files with one hard link are supported')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError('only regular files with one hard link are supported')
        content = stream.read()
        after = os.fstat(stream.fileno())
    current = path.stat()
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_nlink)
    if signature(initial) != signature(before) or signature(before) != signature(after) or signature(after) != signature(current):
        raise ValueError('source changed during read')
    return content


def copy_verified(source, target, expected):
    content = read_bytes(source)
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError('private bundle integrity failure')
    target = safe_path(target)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb') as stream:
        stream.write(content)
    if digest(target) != expected:
        raise ValueError('copied material integrity failure')


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('x', encoding='utf-8') as f:
        os.chmod(path, 0o600)
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')


def digest(path):
    return hashlib.sha256(read_bytes(path)).hexdigest()


def map_values(value, transform):
    if isinstance(value, dict):
        return {k: map_values(v, transform) for k, v in value.items()}
    if isinstance(value, list):
        return [map_values(v, transform) for v in value]
    return transform(value) if isinstance(value, str) else value


def export(config, destination):
    config = safe_path(config)
    settings = json.loads(read_bytes(config))
    state = Path(settings['state_dir']).expanduser()
    if not state.is_absolute():
        state = config.parent / state
    cloud = settings.get('browser_mode') == 'cloud_host'
    dbpath = state / ('cloud-batches.sqlite3' if cloud else 'tasks.sqlite3')
    safe_path(dbpath)
    read_bytes(dbpath)  # Reject links before opening the existing database read-only.
    with sqlite3.connect(dbpath.absolute().as_uri() + '?mode=ro', uri=True) as db:
        db.execute('BEGIN')
        if cloud:
            meta = dict(db.execute('SELECT key,value FROM cloud_batch_meta'))
            if meta.get('account') != settings['account_id'] or meta.get('expected_user_id') != settings['expected_user_id']:
                raise ValueError('cloud business profile identity mismatch')
        policy = json.loads(db.execute('SELECT body FROM ops_policy WHERE id=1').fetchone()[0])
        version = policy['content_profile_version']
        profile = json.loads(db.execute('SELECT body FROM ops_profile_versions WHERE version=?', (version,)).fetchone()[0])
    # Validate all required file references before creating the destination.
    sources = set()
    def collect(value):
        if value.startswith('/') and Path(value).is_file():
            sources.add(value)
        for match in re.findall(r'(/(?:Users|home)/[^；\n]+?\.(?:json|md))', value):
            if Path(match).is_file():
                sources.add(match)
        return value
    map_values(profile, collect)
    required = [x['file'] for x in profile.get('product_materials', [])]
    if profile.get('face_reference'):
        required.append(profile['face_reference']['file'])
    for name in required:
        if name not in sources:
            raise ValueError('required material is missing')
    for item in profile.get('product_materials', []) + ([profile['face_reference']] if profile.get('face_reference') else []):
        if digest(Path(item['file'])) != item['sha256']:
            raise ValueError('private material hash mismatch')
    for name in sources:
        source = safe_path(name)
        if source == config or source == safe_path(dbpath) or (
                source.name.lower() in {'cookies.json', 'credentials.json', 'token.json', 'tokens.json', '.env'}
                or source.name.startswith('.env.') or source.suffix.lower() in {'.pem', '.key', '.p12', '.pfx'}):
            raise ValueError('configuration or credential file cannot be a portable material')
    source_digests = {name: digest(Path(name)) for name in sources}
    for item in profile.get('product_materials', []) + ([profile['face_reference']] if profile.get('face_reference') else []):
        if source_digests[item['file']] != item['sha256']:
            raise ValueError('private material changed before export')
    preferences = portable_preferences(settings)
    destination = safe_path(destination)
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    assets = destination / 'assets'; assets.mkdir(mode=0o700)
    mapping = {}
    for name in sorted(sources):
        src = Path(name)
        rel = 'assets/' + source_digests[name] + '-' + src.name
        if not (destination / rel).exists():
            copy_verified(src, destination / rel, source_digests[name])
        mapping[name] = rel
    def portable(value):
        for old in sorted(mapping, key=len, reverse=True):
            value = value.replace(old, '${PRIVATE_ROOT}/' + mapping[old])
        return value
    write_json(destination / 'content-profile.json', map_values(profile, portable))
    # Full settings can contain credentials: never export them implicitly.
    write_json(destination / 'transfer.json', {
        'format': 1, 'profile_version': version, 'profile_sha256': digest(destination / 'content-profile.json'), 'same_host_config': str(config),
        'account_id': settings['account_id'], 'expected_user_id': settings['expected_user_id'],
        'runtime_preferences': preferences,
        'allowed_tools': policy['allowed_tools'],
        'quality_standard': '完整读取当前私人产品原文，执行当前运营SKILL及内容审查，不沿用旧批次临时限制。',
        'files': {rel: digest(destination / rel) for rel in sorted(set(mapping.values()))},
        'contains_login': False, 'contains_task_history': False,
        'contains_full_config': False,
    })
    write_json(destination / 'source-path-map.json', mapping)
    return {'status': 'exported', 'destination': str(destination), 'assets': len(mapping), 'profile_version': version}


def prepare(bundle, destination, plugin_root):
    bundle = safe_path(bundle); plugin_root = safe_path(plugin_root)
    metadata = json.loads(read_bytes(bundle / 'transfer.json'))
    profile_bytes = read_bytes(bundle / 'content-profile.json')
    if hashlib.sha256(profile_bytes).hexdigest() != metadata['profile_sha256']:
        raise ValueError('private profile integrity failure')
    if metadata.get('format') != 1 or not isinstance(metadata.get('files'), dict):
        raise ValueError('unsupported private bundle')
    verified = {}
    for name, expected in metadata['files'].items():
        if not isinstance(name, str) or not re.fullmatch(r'assets/[^/\\]+', name) or name.split('/')[-1] in ('.', '..'):
            raise ValueError('unsafe private bundle path')
        path = safe_path(bundle / name)
        if digest(path) != expected:
            raise ValueError('private bundle integrity failure')
        verified[name] = expected
    assets = safe_path(bundle / 'assets')
    if not assets.is_dir():
        raise ValueError('private bundle assets missing')
    actual = set()
    for path in assets.rglob('*'):
        safe_path(path)
        if not path.is_file():
            raise ValueError('unexpected private bundle directory')
        read_bytes(path)
        actual.add(path.relative_to(bundle).as_posix())
    if actual != set(verified):
        raise ValueError('unlisted private bundle assets')
    if not (plugin_root / 'runtime/xhs_ops.py').is_file() or not (plugin_root / 'bin/xhs-mcp-custom').is_file():
        raise ValueError('choose a complete platform runtime package')
    destination = safe_path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError('destination already exists; existing accounts are never overwritten')
    if destination.resolve().is_relative_to(plugin_root):
        raise ValueError('private files must stay outside plugin')
    config = json.loads(read_bytes(plugin_root / 'config/xhs.example.json'))
    config.update(portable_preferences(metadata['runtime_preferences']))
    if config.get('account_id') != metadata['account_id'] or config.get('expected_user_id') != metadata['expected_user_id']:
        raise ValueError('private bundle identity mismatch')
    raw_profile = json.loads(profile_bytes)
    def validate_reference(value):
        if value.startswith('${PRIVATE_ROOT}/'):
            if value[len('${PRIVATE_ROOT}/'):] not in verified:
                raise ValueError('profile file reference missing from manifest')
        elif '${PRIVATE_ROOT}/' in value:
            # Embedded prose is not a reliably parseable file manifest. Reject
            # explicit traversal even there; callers must supply exact references.
            for suffix in value.split('${PRIVATE_ROOT}/')[1:]:
                if '..' in suffix.split('/') or suffix.startswith('/'):
                    raise ValueError('unsafe embedded private material reference')
        return value
    map_values(raw_profile, validate_reference)
    required = list(raw_profile.get('product_materials', []))
    if raw_profile.get('face_reference'):
        required.append(raw_profile['face_reference'])
    for item in required:
        name = item['file']
        if not isinstance(name, str) or not name.startswith('${PRIVATE_ROOT}/'):
            raise ValueError('material must reference verified private bundle')
        name = name[len('${PRIVATE_ROOT}/'):]
        if verified.get(name) != item['sha256']:
            raise ValueError('required material missing from manifest')
    destination.mkdir(parents=True, mode=0o700)
    (destination / 'assets').mkdir(mode=0o700)
    for name, expected in verified.items():
        copy_verified(bundle / name, destination / name, expected)
    profile = map_values(raw_profile,
                         lambda s: s.replace('${PRIVATE_ROOT}', str(destination.resolve())))
    config.update(state_dir=str(destination / 'state'), output_dir=str(destination / 'output'),
                  mcp_binary=str(plugin_root / 'bin/xhs-mcp-custom'), expected_state_id='',
                  allow_write=False, allow_reconcile=False)
    for name in ('state', 'output'):
        (destination / name).mkdir(mode=0o700)
    write_json(destination / 'xhs.local.json', config)
    write_json(destination / 'configure-profile.json', {
        'authorization_ref': 'Imported business settings only; this is not authorization to publish or repeat old tasks.',
        'quality_standard': metadata['quality_standard'], 'allowed_tools': metadata['allowed_tools'],
        'content_profile': profile,
    })
    return {'status': 'prepared', 'config': str(destination / 'xhs.local.json'),
            'profile_input': str(destination / 'configure-profile.json'),
            'account_identity_to_verify': metadata['expected_user_id'],
            'state_initialized': False, 'write_enabled': False,
            'note': 'Same-host continuation must use the original config and state. New-host setup requires login/identity verification; this bundle contains no prior tasks.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    e = sub.add_parser('export');e.add_argument('--config', type=Path, required=True);e.add_argument('--destination', type=Path, required=True)
    i = sub.add_parser('prepare');i.add_argument('--bundle', type=Path, required=True);i.add_argument('--destination', type=Path, required=True);i.add_argument('--plugin-root', type=Path, default=ROOT)
    a = p.parse_args()
    result = export(a.config, a.destination) if a.action == 'export' else prepare(a.bundle, a.destination, a.plugin_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
