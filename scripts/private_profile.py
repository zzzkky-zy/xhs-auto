#!/usr/bin/env python3
"""Export current private business profile; prepare relocatable files without touching live state."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('x', encoding='utf-8') as f:
        os.chmod(path, 0o600)
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def map_values(value, transform):
    if isinstance(value, dict):
        return {k: map_values(v, transform) for k, v in value.items()}
    if isinstance(value, list):
        return [map_values(v, transform) for v in value]
    return transform(value) if isinstance(value, str) else value


def export(config, destination):
    config = config.expanduser().resolve()
    settings = json.loads(config.read_text())
    state = Path(settings['state_dir']).expanduser()
    if not state.is_absolute():
        state = config.parent / state
    dbpath = state / 'tasks.sqlite3'
    with sqlite3.connect(dbpath.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.execute('BEGIN')
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
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    assets = destination / 'assets'; assets.mkdir(mode=0o700)
    mapping = {}
    for name in sorted(sources):
        src = Path(name)
        rel = 'assets/' + digest(src)[:16] + '-' + src.name
        shutil.copyfile(src, destination / rel)
        os.chmod(destination / rel, 0o600)
        mapping[name] = rel
    def portable(value):
        for old in sorted(mapping, key=len, reverse=True):
            value = value.replace(old, '${PRIVATE_ROOT}/' + mapping[old])
        return value
    write_json(destination / 'content-profile.json', map_values(profile, portable))
    # Preserve full settings as a private snapshot for same-host continuation only.
    write_json(destination / 'same-host-settings.json', settings)
    write_json(destination / 'transfer.json', {
        'format': 1, 'profile_version': version, 'profile_sha256': digest(destination / 'content-profile.json'), 'same_host_config': str(config),
        'account_id': settings['account_id'], 'expected_user_id': settings['expected_user_id'],
        'runtime_preferences': {k: v for k, v in settings.items() if k not in {
            'state_dir', 'output_dir', 'mcp_binary', 'mcp_url', 'listen', 'cdp_url',
            'expected_state_id', 'allow_write', 'allow_reconcile', 'auth_token_env'}},
        'allowed_tools': policy['allowed_tools'],
        'quality_standard': '完整读取当前私人产品原文，执行当前运营SKILL及内容审查，不沿用旧批次临时限制。',
        'files': {rel: digest(destination / rel) for rel in sorted(set(mapping.values()))},
        'contains_login': False, 'contains_task_history': False,
    })
    write_json(destination / 'source-path-map.json', mapping)
    return {'status': 'exported', 'destination': str(destination), 'assets': len(mapping), 'profile_version': version}


def prepare(bundle, destination, plugin_root):
    bundle = bundle.expanduser().resolve(); plugin_root = plugin_root.expanduser().resolve()
    metadata = json.loads((bundle / 'transfer.json').read_text())
    if digest(bundle / 'content-profile.json') != metadata['profile_sha256']:
        raise ValueError('private profile integrity failure')
    for name, expected in metadata['files'].items():
        path = (bundle / name).resolve()
        if not path.is_relative_to(bundle) or not path.is_file() or digest(path) != expected:
            raise ValueError('private bundle integrity failure')
    if not (plugin_root / 'runtime/xhs_ops.py').is_file() or not (plugin_root / 'bin/xhs-mcp-custom').is_file():
        raise ValueError('choose a complete platform runtime package')
    destination = destination.expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError('destination already exists; existing accounts are never overwritten')
    if destination.resolve().is_relative_to(plugin_root):
        raise ValueError('private files must stay outside plugin')
    destination.mkdir(parents=True, mode=0o700)
    shutil.copytree(bundle / 'assets', destination / 'assets')
    for p in (destination / 'assets').rglob('*'):
        os.chmod(p, 0o700 if p.is_dir() else 0o600)
    profile = map_values(json.loads((bundle / 'content-profile.json').read_text()),
                         lambda s: s.replace('${PRIVATE_ROOT}', str(destination.resolve())))
    config = json.loads((plugin_root / 'config/xhs.example.json').read_text())
    config.update(metadata['runtime_preferences'])
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
