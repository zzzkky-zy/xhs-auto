"""Fail closed before opening executable ledgers from isolated recovery copies."""
import os
from pathlib import Path

MARKER = 'RECOVERY-QUARANTINE.json'

def assert_not_quarantined(state_dir, config_dir=None):
    path = Path(state_dir).expanduser()
    if not path.is_absolute():
        path = Path(config_dir or '.') / path
    # Existence, not trusted marker contents: malformed or linked markers also stop.
    path = Path(os.path.abspath(path))
    for parent in (path, *path.parents):
        marker = parent / MARKER
        try:
            os.lstat(marker)
        except FileNotFoundError:
            continue
        else:
            raise ValueError('ERR_RECOVERY_QUARANTINED: isolated recovery copy is read-only; ordinary unpause cannot activate it')
