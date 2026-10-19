import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import queue
import shutil
import threading
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("bootstrap_ops", ROOT / "runtime/xhs_ops.py")
OPS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OPS)


@unittest.skipUnless((ROOT / "bin/xhs-mcp-custom").exists(), "build native binary first")
class BootstrapTests(unittest.TestCase):
    def setUp(self):
        import socket
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
        self.private = self.path / "config.json"
        self.private.write_text(json.dumps({"schema_version": 1, "account_id": "bootstrap-fixture", "state_dir": "state", "output_dir": "output", "mcp_binary": str(ROOT / "bin/xhs-mcp-custom"), "listen": "127.0.0.1:%d" % port, "mcp_url": "http://127.0.0.1:%d/mcp" % port, "cdp_url": "http://127.0.0.1:1", "allow_write": False, "timeout_seconds": 3}))
        OPS.prepare_state(self.private)
        self.config = OPS.Config(self.private)

    def tearDown(self):
        try:
            OPS.service_command(self.config, "stop")
        finally:
            self.temp.cleanup()

    def test_dead_record_recovery_preserves_queue_and_binding(self):
        finished = subprocess.Popen([sys.executable, "-c", "pass"]); finished.wait()
        record = self.config.state_dir / "managed-process.json"
        record.write_text(json.dumps({"pid": finished.pid, "child_pid": finished.pid, "config": str(self.config.path)}))
        self.assertEqual(OPS.service_command(self.config, "status")["status"], "stale")
        store = OPS.Store(self.config)
        task = store.enqueue("check_login_status", {})["id"]
        store.db.close()
        self.assertEqual(OPS.service_command(self.config, "ensure")["status"], "started")
        self.assertTrue(OPS.backend_ready(self.config))
        restarted = OPS.Store(self.config)
        try:
            restarted.verify_state()
            self.assertEqual(restarted.task(task)["status"], "queued")
        finally: restarted.db.close()

    def test_parallel_ensure_starts_only_one_backend(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda _: OPS.service_command(self.config, "ensure")["status"], range(2)))
        self.assertEqual(sorted(statuses), ["ready", "started"])

    def test_foreground_host_service_is_ready_stoppable_and_preserves_binding(self):
        command = [sys.executable, str(ROOT / "runtime/xhs_ops.py"), "--config", str(self.private), "service", "foreground"]
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(command, stdout=output, stderr=output)
            try:
                deadline = time.monotonic()+8
                while time.monotonic()<deadline and process.poll() is None:
                    if OPS.backend_ready(self.config): break
                    time.sleep(0.05)
                self.assertTrue(OPS.backend_ready(self.config))
                self.assertEqual(OPS.service_command(self.config, "stop")["status"], "stopped")
                self.assertEqual(process.wait(timeout=8), 0)
                store = OPS.Store(self.config)
                try: store.verify_state()
                finally: store.db.close()
            finally:
                if process.poll() is None:
                    process.terminate(); process.wait(timeout=8)

    def test_live_pid_with_unlocked_record_is_not_touched(self):
        record = self.config.state_dir / "managed-process.json"
        value = json.dumps({"pid": os.getpid(), "child_pid": os.getpid(), "config": str(self.config.path)})
        record.write_text(value)
        try:
            with self.assertRaisesRegex(ValueError, "ERR_SERVICE_IDENTITY"):
                OPS.service_command(self.config, "ensure")
            self.assertEqual(record.read_text(), value)
        finally: record.unlink()

    def test_foreign_service_identity_is_rejected(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"data":{"account":"other"}}'
        with mock.patch.object(OPS.urllib.request, "urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "ERR_SERVICE_IDENTITY"):
                OPS.service_command(self.config, "ensure")
        self.assertFalse((self.config.state_dir / "managed-process.json").exists())

    def test_start_requires_ready_service_not_just_pid_file(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"data":{"account":"other"}}'
        with mock.patch.object(OPS.urllib.request, "urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "ERR_SERVICE_IDENTITY"):
                OPS.service_command(self.config, "start")
        self.assertEqual(OPS.managed_status(self.config)["status"], "not_managed")

    def test_identical_runtime_copy_can_stop_owned_backend(self):
        copy_root = self.path / "plugin-copy"
        (copy_root / "runtime").mkdir(parents=True)
        shutil.copyfile(ROOT / "VERSION", copy_root / "VERSION")
        copy_script = copy_root / "runtime/xhs_ops.py"
        shutil.copyfile(ROOT / "runtime/xhs_ops.py", copy_script)
        shutil.copyfile(ROOT / "runtime/continuous_ops.py", copy_root / "runtime/continuous_ops.py")
        shutil.copyfile(ROOT / "runtime/content_review.py", copy_root / "runtime/content_review.py")
        command = [sys.executable, str(copy_script), "--config", str(self.private), "service"]
        started = subprocess.run(command + ["ensure"], capture_output=True, text=True, timeout=20)
        self.assertEqual(started.returncode, 0, started.stderr)
        try:
            self.assertEqual(OPS.service_command(self.config, "stop")["status"], "stopped")
        finally:
            subprocess.run(command + ["stop"], capture_output=True, timeout=15)

    @mock.patch.dict(os.environ, {"XHS_SEARCH_INTERVAL": "300s", "XHS_READ_INTERVAL": "200s"})
    def test_idle_ensure_applies_real_changed_pacing_and_preserves_queue(self):
        OPS.service_command(self.config, "ensure")
        # Parent-shell legacy variables must not override the private config
        # or silently escape its fingerprint on a real process launch.
        initial = OPS.runtime_snapshot(self.config)["request_policy"]
        self.assertEqual((initial["search_interval_min_seconds"], initial["search_interval_max_seconds"]), (60, 90))
        self.assertEqual((initial["read_interval_min_seconds"], initial["read_interval_max_seconds"]), (10, 20))
        original = json.loads((self.config.state_dir / "managed-process.json").read_text())
        store = OPS.Store(self.config)
        try:
            item = store.enqueue("get_runtime_status", {})
        finally:
            store.db.close()
        data = json.loads(self.private.read_text())
        data.update(write_interval_min_seconds=75, write_interval_max_seconds=175,
                    search_interval_min_seconds=65, search_interval_max_seconds=95,
                    read_interval_min_seconds=12, read_interval_max_seconds=22)
        self.private.write_text(json.dumps(data))
        changed = OPS.Config(self.private)
        with self.assertRaisesRegex(ValueError, "ERR_CONFIG_STALE"):
            OPS.backend_ready(changed)
        self.assertEqual(OPS.service_command(changed, "ensure")["status"], "reloaded")
        actual = OPS.runtime_snapshot(changed)
        self.assertEqual(actual["config_fingerprint"], changed.fingerprint())
        self.assertEqual(actual["request_policy"]["write_interval_min_seconds"], 75)
        self.assertEqual(actual["request_policy"]["write_interval_max_seconds"], 175)
        self.assertEqual(actual["request_policy"]["search_interval_min_seconds"], 65)
        self.assertEqual(actual["request_policy"]["search_interval_max_seconds"], 95)
        self.assertEqual(actual["request_policy"]["read_interval_min_seconds"], 12)
        self.assertEqual(actual["request_policy"]["read_interval_max_seconds"], 22)
        self.assertNotEqual(json.loads((changed.state_dir / "managed-process.json").read_text())["pid"], original["pid"])
        store = OPS.Store(changed)
        try:
            self.assertEqual(store.task(item["id"])["status"], "queued")
        finally:
            store.db.close()

    def test_config_reload_leaves_running_lease_and_process_untouched(self):
        OPS.service_command(self.config, "ensure")
        record = (self.config.state_dir / "managed-process.json").read_text()
        store = OPS.Store(self.config)
        try:
            store.enqueue("get_runtime_status", {})
            task = store.claim()
            data = json.loads(self.private.read_text())
            data["search_interval_seconds"] = 80
            self.private.write_text(json.dumps(data))
            changed = OPS.Config(self.private)
            with self.assertRaisesRegex(ValueError, "ERR_CONFIG_PENDING_RELOAD"):
                OPS.service_command(changed, "ensure")
            self.assertEqual((changed.state_dir / "managed-process.json").read_text(), record)
            self.assertEqual(store.task(task["id"])["status"], "running")
        finally:
            store.db.close()

    def test_stdio_cold_start_and_backend_restart(self):
        process = subprocess.Popen([sys.executable, str(ROOT / "runtime/codex_mcp.py"), "--config", str(self.private)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        messages = queue.Queue()
        def read_messages():
            for line in process.stdout: messages.put(json.loads(line))
            messages.put(None)
        reader = threading.Thread(target=read_messages, daemon=True); reader.start()
        def request(number, method, params):
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": number, "method": method, "params": params}) + "\n"); process.stdin.flush()
            while True:
                response = messages.get(timeout=30)
                if response is None: raise AssertionError("stdio closed: " + process.stderr.read()[-800:])
                if "id" in response: break
                self.assertTrue(response.get("method", "").startswith("notifications/"))
            self.assertEqual(response["id"], number)
            return response["result"]
        try:
            initial = request(1, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "fixture", "version": "1"}})
            self.assertEqual(initial["serverInfo"]["version"], (ROOT / "VERSION").read_text().strip())
            process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n'); process.stdin.flush()
            tools = request(2, "tools/list", {})["tools"]
            self.assertEqual(len(tools), 30)
            self.assertIn("reconcile_task", {tool["name"] for tool in tools})
            # Exercise the normal stdio -> Go -> Python receipt path. A missing
            # task must be rejected without navigating or creating a send.
            receipt = request(21, "tools/call", {"name": "reconcile_task", "arguments": {"task_id": "fixture-absent"}})
            self.assertTrue(receipt["isError"])
            self.assertIn("task not found", json.dumps(receipt))
            OPS.service_command(self.config, "stop")
            result = request(3, "tools/call", {"name": "get_operation_status", "arguments": {"operation_id": "fixture-absent"}})
            self.assertTrue(result["isError"])
            self.assertIn("ERR_OPERATION_NOT_FOUND", json.dumps(result))
            result = request(4, "tools/call", {"name": "check_login_status", "arguments": {}})
            self.assertIn("ERR_BROWSER_UNAVAILABLE", json.dumps(result))
            self.assertTrue(OPS.backend_ready(self.config))
            # The same connected proxy notices private config edits; no Codex
            # reconnect is required for an ordinary idle pacing change.
            data = json.loads(self.private.read_text())
            data.update(write_interval_min_seconds=80, write_interval_max_seconds=170)
            self.private.write_text(json.dumps(data))
            result = request(5, "tools/call", {"name": "get_runtime_status", "arguments": {}})
            self.assertFalse(result.get("isError", False), result)
            self.assertTrue(OPS.backend_ready(OPS.Config(self.private)))
        finally:
            process.stdin.close()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
            reader.join(timeout=2)
            process.stdout.close(); process.stderr.close()
