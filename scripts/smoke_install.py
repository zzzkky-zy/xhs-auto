#!/usr/bin/env python3
"""从本机架构安装包干净解包，验清单和启停；不登录、不访问小红书。"""
import argparse
import hashlib
import importlib.util
import json
import queue
from pathlib import Path, PurePosixPath
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import threading

REQUIRED_CAPABILITIES = {
    "post_comment_to_feed", "reply_comment_in_feed", "publish_content", "publish_with_video",
    "get_runtime_status", "get_operation_status", "submit_task", "get_task_status",
    "control_task_queue", "manage_operation_run", "reconcile_task", "resume_task",
}


def probe_packaged_stdio(root, private, version, exercise_tasks=False, measurements=None):
    process = subprocess.Popen([sys.executable, str(root / "runtime/codex_mcp.py"), "--config", str(private)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    messages = queue.Queue()
    def read():
        for line in process.stdout:
            try: messages.put(json.loads(line))
            except ValueError: messages.put({"protocol_error": True})
        messages.put(None)
    reader = threading.Thread(target=read, daemon=True); reader.start()
    def call(number, method, params):
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": number, "method": method, "params": params}) + "\n"); process.stdin.flush()
        while True:
            response = messages.get(timeout=30)
            if response is None or "protocol_error" in response:
                raise ValueError("packaged stdio closed or emitted non-protocol output")
            if "id" in response:
                if response["id"] != number or "result" not in response: raise ValueError("stdio response mismatch")
                return response["result"]
            if not response.get("method", "").startswith("notifications/"): raise ValueError("unexpected stdio message")
    try:
        result = call(1, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "install-fixture", "version": "1"}})
        if result["serverInfo"]["version"] != version: raise ValueError("stdio version mismatch")
        process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n'); process.stdin.flush()
        tools = call(2, "tools/list", {})["tools"]
        if measurements is not None:
            measurements["capabilities"] = sorted(tool["name"] for tool in tools)
        result = call(3, "tools/call", {"name": "get_runtime_status", "arguments": {}})
        if result.get("isError"): raise ValueError("stdio diagnostic routing failed")
        diagnostic = json.loads(next(item["text"] for item in result["content"] if item.get("type") == "text"))
        if diagnostic["version"] != version: raise ValueError("runtime version mismatch")
        if exercise_tasks:
            start = time.monotonic()
            submitted = call(4, "tools/call", {"name": "submit_task", "arguments": {"tool": "get_runtime_status", "payload": {}}})
            elapsed = time.monotonic() - start
            if submitted.get("isError"): raise ValueError("task submission failed")
            task_id = json.loads(submitted["content"][0]["text"])["id"]
            number = 5
            until = time.monotonic() + 20
            while True:
                result = call(number, "tools/call", {"name": "get_task_status", "arguments": {"task_id": task_id, "include_result": True}})
                if result.get("isError"): raise ValueError("task status failed")
                task = json.loads(result["content"][0]["text"])
                number += 1
                if task["status"] == "succeeded": break
                if task["status"] in {"unknown", "blocked", "failed"} or time.monotonic() > until: raise ValueError("normal local task did not complete")
                time.sleep(0.1)
            if "progress" not in task or task["progress"]["status"] != "succeeded": raise ValueError("task progress not persisted")
            paused = call(number, "tools/call", {"name": "control_task_queue", "arguments": {"enabled": False}})
            if paused.get("isError"): raise ValueError("queue pause failed")
            if measurements is not None: measurements.update(task_submit_seconds=round(elapsed,4), task_end_to_end_seconds=round(time.monotonic()-start,4), task_status=task["status"], progress_stage=task["progress"]["stage"], queued_site_actions=0)
        return len(tools)
    finally:
        process.stdin.close()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
        reader.join(timeout=2)
        process.stdout.close(); process.stderr.close()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--normal-only", action="store_true", help="only normal local MCP/task flow; no injected failures or write attempts")
    args = parser.parse_args()
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="xhs-install-fixture-") as tmp:
        base = Path(tmp)
        with tarfile.open(args.archive) as archive:
            for member in archive.getmembers():
                parts = PurePosixPath(member.name)
                if parts.is_absolute() or ".." in parts.parts or not member.isfile():
                    raise ValueError("unsafe archive entry")
            archive.extractall(base)
        roots = list(base.iterdir())
        if len(roots) != 1:
            raise ValueError("archive requires one root")
        root = roots[0]
        manifest = root / "MANIFEST.sha256"
        checked = 0
        manifest_names = set()
        for line in manifest.read_text().splitlines():
            expected, name = line.split("  ", 1)
            if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
                raise ValueError("manifest mismatch: " + name)
            manifest_names.add(name)
            checked += 1
        content_review = root / "runtime/content_review.py"
        if "from content_review import ContentReview" in (root / "runtime/continuous_ops.py").read_text():
            if not content_review.is_file() or "runtime/content_review.py" not in manifest_names:
                raise ValueError("packaged content review dependency missing from manifest")
        info = json.loads((root / "BUILD-INFO.json").read_text())
        spec = importlib.util.spec_from_file_location("xhs_install_fixture", root / "runtime/xhs_ops.py")
        ops = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ops)
        config_data = json.loads((root / "config/xhs.example.json").read_text())
        port, cdp_port = free_port(), free_port()
        config_data.update({"account_id": "install-fixture", "allow_write": False, "allow_reconcile": False, "expected_user_id": "", "auth_token_env": "XHS_INSTALL_FIXTURE_UNUSED_TOKEN", "listen": "127.0.0.1:%d" % port, "mcp_url": "http://127.0.0.1:%d/mcp" % port, "cdp_url": "http://127.0.0.1:%d" % cdp_port, "timeout_seconds": 5})
        private = root / "config/install-fixture.local.json"
        private.write_text(json.dumps(config_data))
        ops.prepare_state(private)
        config = ops.Config(private)
        process_started = False
        try:
            ops.service_command(config, "start")
            process_started = True
            until = time.monotonic() + 5
            status = ops.health(config)
            while status["mcp"] != "reachable" and time.monotonic() < until:
                time.sleep(0.1)
                status = ops.health(config)
            if status["mcp"] != "reachable" or status["write_enabled"] or status["logged_in"] != "not_checked":
                raise ValueError("isolated health failed")
            if status["cdp"] != "unavailable":
                raise ValueError("fixture port unexpectedly provides browser; stop acceptance")
            client = ops.MCPTransport(config)
            client.connect()
            tools = client.request("tools/list", {})["tools"]
            names = {tool["name"] for tool in tools}
            if not REQUIRED_CAPABILITIES.issubset(names):
                raise ValueError("tool list incomplete")
            if not args.normal_only:
                try:
                    client.call("post_comment_to_feed", {"feed_id": "install-fixture", "xsec_token": "dummy", "content": "模拟评论", "operation_id": "install-fixture"})
                except ops.ToolFailure as error:
                    if error.code != "ERR_WRITE_NOT_ALLOWED":
                        raise
                else:
                    raise ValueError("default write gate not enforced")
            store = ops.Store(config)
            try:
                if store.status()["counts"]:
                    raise ValueError("unexpected tasks")
            finally:
                store.db.close()
            measurements = {}
            stdio_tools = probe_packaged_stdio(root, private, info["version"], exercise_tasks=args.normal_only, measurements=measurements)
            if stdio_tools != len(tools): raise ValueError("stdio tools differ from backend")
        finally:
            if process_started:
                ops.service_command(config, "stop")
        report = {"status": "passed", "seconds": round(time.monotonic()-started, 3), "source_commit": info["source_commit"], "version": info["version"], "target": info["target"], "artifact_kind": info.get("artifact_kind"), "manifest_files_verified": checked, "mcp_tools": len(tools), "stdio_tools": stdio_tools, "real_xhs_operations": 0, "browser_started": False, "cloud_verified": False, "binary_sha256": hashlib.sha256((root / "bin/xhs-mcp-custom").read_bytes()).hexdigest(), "runtime_sha256": hashlib.sha256((root / "runtime/xhs_ops.py").read_bytes()).hexdigest(), "stdio_entry_sha256": hashlib.sha256((root / "runtime/codex_mcp.py").read_bytes()).hexdigest(), "checks": ["clean_unpack", "manifest", "private_state_binding", "managed_start", "health", "mcp_handshake_and_tools", "packaged_stdio_handshake_and_call", "default_write_gate", "empty_queue", "managed_stop"]}
        if content_review.is_file():
            report["content_review_sha256"] = hashlib.sha256(content_review.read_bytes()).hexdigest()
            report["checks"].append("packaged_content_review_module")
    report.update(measurements)
    report["fault_injection"] = False
    report["default_write_gate_exercised"] = not args.normal_only
    if args.normal_only:
        report["checks"] = [c for c in report["checks"] if c != "default_write_gate"] + ["normal_async_local_diagnostic_task", "persisted_progress", "queue_pause"]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
