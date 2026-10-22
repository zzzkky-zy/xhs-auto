"""Private, opt-in synthetic integration failure metadata; never raw payloads."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid
import urllib.error
import urllib.request


def log_summary(text):
    return {'bytes': len(text.encode()), 'sha256': hashlib.sha256(text.encode()).hexdigest(),
            'error_codes': sorted(set(re.findall(r'\bERR_[A-Z0-9_]{1,64}\b', text))),
            'runtime_responses': re.findall(r'GET /runtime (\d{3})', text),
            'gin_durations': re.findall(r'\|\s*\d{3}\s*\|\s*([\d.]+(?:ns|µs|ms|s))\s*\|', text)}


def export_failure(directory, test_name, events, logs, failed):
    if not failed or not directory:
        return None
    directory = Path(os.path.abspath(directory))
    if any(p.is_symlink() for p in (directory, *directory.parents)):
        raise ValueError('diagnostics require unlinked private directory')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if stat.S_IMODE(directory.stat().st_mode) & 0o077:
        raise ValueError('diagnostics require mode 0700 directory')
    record = {'scope': 'synthetic_test_failure_only', 'test': test_name,
              'events': events, 'logs': {name: log_summary(text) for name, text in logs.items()},
              'raw_logs_exported': False, 'proves_cause': False}
    path = directory / ('failure-' + uuid.uuid4().hex + '.json')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as output:
        json.dump(record, output, indent=2); output.write('\n')
    return path


def await_runtime(process, endpoint, events, *, deadline_seconds=5, probe=None, clock=None, sleep=None):
    """One total bound, per-probe budget <= 1s, no changing readiness semantics."""
    probe = probe or urllib.request.urlopen
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    start = clock(); deadline = start + deadline_seconds
    while True:
        if process.poll() is not None:
            raise AssertionError('isolated backend exited before readiness')
        remaining = deadline - clock()
        if remaining <= 0:
            raise AssertionError('isolated backend readiness deadline exceeded')
        began = clock()
        try:
            with probe(endpoint, timeout=min(1.0, remaining)) as response:
                data = json.load(response)
            # A JSON error/empty body must not be mistaken for actual readiness.
            if not isinstance(data, dict) or not isinstance(data.get('data'), dict) or not data['data'].get('state_id'):
                raise AssertionError('isolated backend returned invalid runtime identity')
            if clock() > deadline:
                raise AssertionError('isolated backend readiness deadline exceeded')
            events.append({'stage': 'runtime_ready', 'elapsed': clock() - start, 'probe_seconds': clock() - began})
            return data
        except (OSError, urllib.error.URLError) as error:
            events.append({'stage': 'runtime_probe_failed', 'elapsed': clock() - start,
                           'probe_seconds': clock() - began, 'error_type': type(error).__name__})
            remaining = deadline - clock()
            if remaining <= 0:
                raise AssertionError('isolated backend readiness deadline exceeded') from None
            sleep(min(.025, remaining))
