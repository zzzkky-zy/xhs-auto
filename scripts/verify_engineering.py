#!/usr/bin/env python3
"""Normal local engineering checks only: no fault injection or real site calls."""
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--report", type=Path, default=ROOT / "工作资料/engineering-results.json")
    args = p.parse_args()
    started = time.monotonic()
    go = Path(os.getenv("XHS_GO_BINARY") or str(ROOT / ".tools/go/bin/go"))
    env = os.environ.copy()
    env.update(GOPROXY="off", GOTOOLCHAIN="local")
    records = []
    commands = [
        ("go-build-all", [str(go), "build", "-mod=vendor", "./..."], ROOT / "mcp/xhs-mcp-custom"),
        ("go-vet", [str(go), "vet", "-mod=vendor", "./..."], ROOT / "mcp/xhs-mcp-custom"),
        ("go-pure-logic", [str(go), "test", "-race", "-mod=vendor", "-run", "^TestEngineering", "-count=1", "-json", ".", "./execution"], ROOT / "mcp/xhs-mcp-custom"),
        ("python-pure-logic", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_engineering_pure.py", "-v"], ROOT),
    ]
    for name, command, cwd in commands:
        start = time.monotonic()
        process = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, timeout=120)
        record = dict(name=name, exit_code=process.returncode, seconds=round(time.monotonic()-start, 3))
        if name == "go-pure-logic":
            events = [json.loads(line) for line in process.stdout.splitlines() if line.startswith("{")]
            record["tests_passed"] = sum(e.get("Action") == "pass" and "Test" in e for e in events)
        records.append(record)
        if process.returncode:
            print((process.stdout + process.stderr)[-5000:])
            raise SystemExit(name + " failed")
    spec = importlib.util.spec_from_file_location("normal_smoke", ROOT / "scripts/smoke_install.py")
    smoke = importlib.util.module_from_spec(spec); spec.loader.exec_module(smoke)
    spec = importlib.util.spec_from_file_location("normal_ops", ROOT / "runtime/xhs_ops.py")
    ops = importlib.util.module_from_spec(spec); spec.loader.exec_module(ops)
    with tempfile.TemporaryDirectory(prefix="xhs-normal-engineering-") as directory:
        base = Path(directory)
        data = json.loads((ROOT / "config/xhs.example.json").read_text())
        port = smoke.free_port()
        data.update(account_id="local-engineering", state_dir=str(base / "state"), output_dir=str(base / "output"), mcp_binary=str(ROOT / "bin/xhs-mcp-custom"), listen="127.0.0.1:%d" % port, mcp_url="http://127.0.0.1:%d/mcp" % port, cdp_url="http://127.0.0.1:%d" % smoke.free_port(), auth_token_env="XHS_NORMAL_UNUSED_TOKEN", timeout_seconds=10, allow_write=False)
        private = base / "private.json"; private.write_text(json.dumps(data))
        ops.prepare_state(private)
        cfg = ops.Config(private)
        measurements = {}
        try:
            tools = smoke.probe_packaged_stdio(ROOT, private, (ROOT / "VERSION").read_text().strip(), exercise_tasks=True, measurements=measurements)
            assert smoke.REQUIRED_CAPABILITIES.issubset(measurements["capabilities"])
        finally:
            ops.service_command(cfg, "stop")
    report = dict(version=(ROOT / "VERSION").read_text().strip(), checked_at=dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(), total_seconds=round(time.monotonic()-started, 3), records=records, actual_local_stdio_tools=tools, normal_task=measurements, binary_sha256=hashlib.sha256((ROOT / "bin/xhs-mcp-custom").read_bytes()).hexdigest(), runtime_sha256=hashlib.sha256((ROOT / "runtime/xhs_ops.py").read_bytes()).hexdigest(), stdio_entry_sha256=hashlib.sha256((ROOT / "runtime/codex_mcp.py").read_bytes()).hexdigest(), fault_injection=False, fault_simulation_not_run="user requested real-use observation next", real_site_actions=0, real_login_checked=False, cloud_verified=False, linux_runtime_verified=False)
    report.update(status="passed", actual_local_stdio_capabilities=measurements["capabilities"],
                  source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                  source_dirty=bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()),
                  runtime_files_sha256={path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                       for path in sorted((ROOT / "runtime").glob("*.py"))})
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
