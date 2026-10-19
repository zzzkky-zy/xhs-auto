#!/usr/bin/env python3
"""有限时长假 MCP 持续运行；无真实登录、评论或发布。"""
import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("test_runtime", ROOT / "tests/test_runtime.py")
tests = importlib.util.module_from_spec(spec); spec.loader.exec_module(tests)
ops = tests.ops


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--seconds", type=int, default=180); args = parser.parse_args()
    if not 1 <= args.seconds <= 600: raise SystemExit("local soak must be bounded to 1..600 seconds")
    start = time.monotonic(); counts = {"enqueued": 0, "succeeded": 0, "restarts": 0, "network_faults": 0, "duplicates": 0}
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary); server = ThreadingHTTPServer(("127.0.0.1", 0), tests.FakeMCP)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        tests.FakeMCP.calls = []; tests.FakeMCP.records = {}
        config_path = root / "config.json"
        config_path.write_text(json.dumps({"schema_version": 1, "account_id": "soak-fixture", "state_dir": "state", "output_dir": "output", "mcp_url": "http://127.0.0.1:%s/mcp" % server.server_port, "cdp_url": "http://127.0.0.1:1", "allow_write": False, "timeout_seconds": 2}))
        ops.prepare_state(config_path); config = ops.Config(config_path)
        clock = [time.time()]; store = ops.Store(config, now=lambda: clock[0])
        try:
            i = 0
            while time.monotonic() - start < args.seconds:
                args_payload = {"keyword": "fixture-%d" % i}
                task = store.enqueue("search_feeds", args_payload); counts["enqueued"] += 1
                counts["duplicates"] += int(store.enqueue("search_feeds", args_payload)["duplicate"])
                tests.FakeMCP.outcome = "network" if i % 11 == 0 else "success"
                ops.tick(store, ops.MCPTransport(config))
                if tests.FakeMCP.outcome == "network":
                    counts["network_faults"] += 1; tests.FakeMCP.outcome = "success"; clock[0] += 100; ops.tick(store, ops.MCPTransport(config))
                if i % 7 == 0:
                    store.db.close(); store = ops.Store(config, now=lambda: clock[0]); counts["restarts"] += 1
                assert store.task(task["id"])["status"] == "succeeded"
                counts["succeeded"] += 1
                i += 1; clock[0] += 1; time.sleep(0.5)
            status = store.status()
            assert not status["counts"].get("unknown") and not status["counts"].get("failed")
            report = {"duration_seconds": round(time.monotonic() - start, 3), "requested_seconds": args.seconds, "fixture_only": True, "real_xhs_operations": 0, **counts, "final_status": status}
            out = ROOT / "docs/soak-result.json"; out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
            print(json.dumps(report, ensure_ascii=False))
        finally:
            store.db.close(); server.shutdown(); server.server_close()


if __name__ == "__main__": main()
