#!/usr/bin/env python3
"""Apply the lightweight profile or prepare host limits; never logs in or starts services."""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
from xhs_ops import Config, Store, atomic_file

PROFILE = {"go_memory_limit_mb": 256, "go_max_procs": 2, "state_disk_limit_mb": 512,
           "min_free_disk_mb": 2048, "min_free_inodes": 1024}


def apply_profile(config):
    config.data["resources"] = dict(PROFILE)
    retention = config.data.setdefault("retention", {})
    retention.update(managed_disk_limit_mb=256, abandoned_keep_hours=72,
                     read_keep_hours=24, write_detail_keep_days=7)
    atomic_file(config.path, (json.dumps(config.data, ensure_ascii=False, indent=2)+"\n").encode())
    return {"status": "profile_saved", "account_paths_authorization_and_schedule": "unchanged",
            "reload": "idle service ensure applies process settings; host limits need host activation"}


def quote_unit(value):
    value = str(value)
    if any(c in value for c in "\n\r\0"):
        raise ValueError("invalid systemd argument")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def unit_files(config, browser_unit=None):
    # Units are only generated, never installed/enabled. The host must place its
    # existing dedicated browser in the same slice to include it in the budget.
    units = {
        "xhs-operations.slice": """[Unit]
Description=Shared budget for XHS executor and its dedicated browser
[Slice]
MemoryAccounting=yes
MemoryHigh=2G
MemoryMax=3G
CPUAccounting=yes
CPUQuota=200%
TasksMax=512
""",
        "xhs-operations.service": """[Unit]
Description=XHS persistent task executor
StartLimitIntervalSec=600
StartLimitBurst=5
[Service]
Type=simple
Slice=xhs-operations.slice
ExecStart=%s %s --config %s service foreground
Restart=on-failure
RestartSec=30
TimeoutStopSec=30
KillMode=mixed
UMask=0077
MemoryAccounting=yes
MemoryHigh=384M
MemoryMax=512M
CPUAccounting=yes
CPUQuota=100%%
TasksMax=128
Nice=5
LimitCORE=0
StandardOutput=null
StandardError=null
[Install]
WantedBy=default.target
""" % tuple(quote_unit(v) for v in (sys.executable, ROOT / "runtime/xhs_ops.py", config.path))}
    if browser_unit:
        if not re.fullmatch(r"[A-Za-z0-9@_.-]+\.service", browser_unit) or browser_unit == "xhs-operations.service":
            raise ValueError("provide the host's existing dedicated browser service name")
        units[browser_unit+".d/xhs-resources.conf"] = """[Service]
Slice=xhs-operations.slice
MemoryAccounting=yes
MemoryHigh=1536M
MemoryMax=2560M
CPUAccounting=yes
CPUQuota=150%
TasksMax=256
LimitCORE=0
"""
    return units


def inspect(config):
    report = {"platform": platform.system(), "machine": platform.machine(), "logical_cpus": os.cpu_count(),
              "version": (ROOT / "VERSION").read_text().strip(), "profile": config.resources,
              "model_and_images": "remote; no local GPU/model required",
              "hard_limits": "unverified; only host cgroup limits bound browser/process RSS",
              "real_site_actions": 0}
    if config.state_id and (config.state_dir / "tasks.sqlite3").exists():
        store = Store(config)
        try:
            store.verify_state()
            report["storage"] = store.resources()
        finally:
            store.db.close()
    else:
        report["storage"] = "state not initialized; check after init-state"
    if platform.system() == "Linux":
        meminfo = Path("/proc/meminfo")
        if meminfo.is_file():
            fields = dict(line.split(":", 1) for line in meminfo.read_text().splitlines())
            report["memory_kib"] = {k: int(fields[k].split()[0]) for k in ("MemTotal", "MemAvailable") if k in fields}
        cgroup = Path("/proc/self/cgroup")
        if cgroup.is_file():
            own = next((line[3:] for line in cgroup.read_text().splitlines() if line.startswith("0::")), "")
            base = Path("/sys/fs/cgroup").resolve()
            path = (base / own.lstrip("/")).resolve()
            if path == base or base in path.parents:
                report["current_cgroup"] = {name: (path / name).read_text().strip() for name in
                    ("memory.current", "memory.high", "memory.max", "memory.events", "cpu.max", "pids.current", "pids.max") if (path / name).is_file()}
        report["systemd_available"] = shutil.which("systemctl") is not None
    return report


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("action", choices=["inspect", "apply-profile", "write-units"])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--browser-unit", help="optional existing user service; never creates a browser")
    args = parser.parse_args()
    config = Config(args.config)
    if args.action == "apply-profile":
        result = apply_profile(config)
    elif args.action == "write-units":
        if args.output is None:
            parser.error("write-units needs a new private --output directory")
        units = unit_files(config, args.browser_unit)
        for name in units:
            if (args.output / name).exists():
                raise ValueError("refusing to overwrite an existing unit; review in a new directory")
        for name, body in units.items():
            path = args.output / name
            path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            atomic_file(path, body.encode())
        result = {"status": "generated_only", "files": list(units), "services_started": False,
                  "browser_included": "only after the host applies its optional browser drop-in",
                  "next": "follow docs/CLOUD-RESOURCES.md; do not double-start a host-owned browser"}
    else:
        result = inspect(config)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
