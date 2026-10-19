#!/usr/bin/env python3
"""Prepare private first-use paths only; never replace existing account settings."""
import argparse
import json
import os
from pathlib import Path
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]


def local_timezone():
    candidates = [os.getenv("TZ", "")]
    local = str(Path("/etc/localtime").resolve())
    if "/zoneinfo/" in local:
        candidates.append(local.split("/zoneinfo/", 1)[1])
    for name in candidates:
        try:
            ZoneInfo(name)
            return name
        except (ValueError, ZoneInfoNotFoundError):
            pass
    return None  # Agent resolves the host timezone before scheduling; do not guess.


def prepare(config_path=None):
    default = Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "xhs-operations/xhs.local.json"
    path = Path(config_path or os.getenv("XHS_OPS_CONFIG") or default).expanduser().absolute()
    # lexists also preserves broken links; do not overwrite any existing entry.
    if os.path.lexists(path):
        return {"status": "existing", "config": str(path), "changed": False}
    path = path.parent.resolve() / path.name
    data_root = Path(os.getenv("XDG_DATA_HOME", str(Path.home() / ".local/share"))).expanduser().resolve()
    account = "xhs-" + uuid.uuid4().hex[:12]
    private = data_root / "xhs-operations/accounts" / account
    if private.is_relative_to(ROOT) or path.is_relative_to(ROOT):
        raise ValueError("private configuration and data must stay outside the plugin")
    data = json.loads((ROOT / "config/xhs.example.json").read_text())
    data.update(account_id=account, state_dir=str(private / "state"), output_dir=str(private / "output"),
                mcp_binary=str(ROOT / "bin/xhs-mcp-custom"), expected_user_id="", expected_state_id="",
                allow_write=False, allow_reconcile=False)
    data["operations"].update(activity="", topic_template="", timezone=local_timezone())
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # O_EXCL prevents another first-use process from being overwritten.
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return {"status": "existing", "config": str(path), "changed": False}
    try:
        for directory in (private, private / "state", private / "output", private / "output/onboarding", private / "private-assets"):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = None
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except BaseException:
        if fd is not None:
            os.close(fd)
        path.unlink()  # Only this process's newly created config; no account has been bound.
        raise
    return {"status": "created", "config": str(path), "output_dir": data["output_dir"],
            "private_assets": str(private / "private-assets"), "state_initialized": False,
            "write_enabled": False, "timezone": data["operations"]["timezone"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.config), ensure_ascii=False))
