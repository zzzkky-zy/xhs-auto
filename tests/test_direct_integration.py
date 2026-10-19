"""Real Go/Python direct lifecycle with synthetic receipts and zero site IO.

The Go journal is seeded before startup, so successful calls replay a durable
receipt instead of opening a browser. Only the browser/platform result is a
fixture: stdio, HTTP, authorization, sealing and task accounting are real.
"""
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import test_direct_runtime as direct_fixtures
import test_runtime as fixtures

ops = fixtures.ops
ROOT = Path(__file__).resolve().parents[1]


class UnexpectedNetwork(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.server.calls.append((self.command, self.path))
        self.send_response(503)
        self.end_headers()

    do_POST = do_GET


class DirectIntegrationTests(unittest.TestCase):
    reviewed = direct_fixtures.DirectRuntimeTests.reviewed

    def setUp(self):
        self.binary = ROOT / "bin/xhs-mcp-custom"
        if not self.binary.is_file():
            self.skipTest("build the native MCP binary before integration tests")
        fixtures.RuntimeTests.setUp(self)
        self.now[0] = time.time()
        self.trap = ThreadingHTTPServer(("127.0.0.1", 0), UnexpectedNetwork)
        self.trap.calls = []
        self.thread = threading.Thread(target=self.trap.serve_forever, daemon=True)
        self.thread.start()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.endpoint = "http://127.0.0.1:%s/mcp" % self.port
        self.config.data.update(mcp_binary=str(self.binary), listen="127.0.0.1:%s" % self.port,
                                mcp_url="http://127.0.0.1:%s/mcp" % self.trap.server_port,
                                cdp_url="http://127.0.0.1:%s" % self.trap.server_port,
                                expected_user_id="fixture-user")
        self.process = None
        self.proxy = None

    def tearDown(self):
        self.stop()
        if hasattr(self, "trap"):
            self.trap.shutdown()
            self.trap.server_close()
            self.thread.join(timeout=2)
        fixtures.RuntimeTests.tearDown(self)

    def stop(self):
        if self.proxy is not None:
            self.proxy.stdin.close()
            try:
                self.proxy.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proxy.kill()
                self.proxy.wait(timeout=3)
            self.proxy.stdout.close()
            self.reader.join(timeout=2)
            self.proxy = None
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
            self.process = None
        for name in ("backend_log", "proxy_log"):
            stream = getattr(self, name, None)
            if stream is not None:
                stream.close()

    def start(self):
        self.config_path.write_text(json.dumps(self.config.data))
        config = ops.Config(self.config_path)
        env = os.environ.copy()
        env.update(config.runtime_env())
        env.update(XHS_OPS_PYTHON=sys.executable, XHS_OPS_SCRIPT=str(ROOT / "runtime/xhs_ops.py"),
                   XHS_OPS_CONFIG=str(self.config_path), XHS_CONFIG_FINGERPRINT=config.fingerprint(),
                   XHS_CONFIG_FILE_SHA256=hashlib.sha256(self.config_path.read_bytes()).hexdigest(),
                   XHS_MCP_URL=self.endpoint, XHS_OPS_QUEUE_PENDING="0", XHS_OPS_STATE_MAINTENANCE="0")
        env.pop("XHS_LAUNCH_BROWSER", None)
        self.backend_log = (self.root / "backend.log").open("w+")
        self.process = subprocess.Popen([str(self.binary), "-port", self.config.data["listen"]], env=env,
                                        stdout=self.backend_log, stderr=subprocess.STDOUT)
        until = time.monotonic() + 5
        while True:
            try:
                with urllib.request.urlopen(self.endpoint[:-4] + "/runtime", timeout=0.2) as response:
                    json.load(response)
                break
            except (OSError, urllib.error.URLError):
                if self.process.poll() is not None or time.monotonic() > until:
                    self.backend_log.seek(0)
                    self.fail("isolated backend did not start: " + self.backend_log.read())
                time.sleep(0.025)
        self.proxy_log = (self.root / "proxy.log").open("w+")
        self.proxy = subprocess.Popen([str(self.binary), "-stdio-proxy", "-upstream", self.endpoint], env=env,
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.proxy_log,
                                      text=True, bufsize=1)
        self.messages = queue.Queue()

        def read():
            for line in self.proxy.stdout:
                try:
                    self.messages.put(json.loads(line))
                except ValueError:
                    self.messages.put({"bad_protocol": line})
            self.messages.put(None)

        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        self.sequence = 0
        initialized = self.rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                               "clientInfo": {"name": "synthetic-direct-integration", "version": "1"}})
        self.assertEqual(initialized["serverInfo"]["version"], (ROOT / "VERSION").read_text().strip())
        self.proxy.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        self.proxy.stdin.flush()

    def rpc(self, method, params):
        self.sequence += 1
        self.proxy.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.sequence, "method": method, "params": params}) + "\n")
        self.proxy.stdin.flush()
        while True:
            value = self.messages.get(timeout=12)
            self.assertIsNotNone(value, "stdio exited before its response")
            self.assertNotIn("bad_protocol", value)
            if "id" in value:
                self.assertEqual(value["id"], self.sequence)
                self.assertNotIn("error", value)
                return value["result"]

    def call(self, tool, arguments):
        return self.rpc("tools/call", {"name": tool, "arguments": arguments})

    def assert_terminal_conflicts(self, tool, arguments, changes):
        ident = arguments["operation_id"]
        before = self.store.task(ident)
        events = self.store.db.execute("SELECT count(*) FROM events WHERE task_id=?", (ident,)).fetchone()[0]
        for changed in changes:
            with self.subTest(changed_fields=sorted(changed)):
                rejected = self.call(tool, {**arguments, **changed})
                self.assertTrue(rejected.get("isError"), rejected)
                self.assertIn("ERR_RECEIPT_MISMATCH", json.dumps(rejected))
                self.assertEqual(self.store.task(ident), before)
                self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE task_id=?", (ident,)).fetchone()[0], events)

    def test_four_tools_go_stdio_to_actual_python_accounts_once_and_http_replays_original_ids(self):
        # The configured MCP URL and CDP endpoint are traps. A Python callback
        # or browser connection fails this test, even if its error were hidden.
        old = self.store.enqueue("post_comment_to_feed", {"feed_id": "older", "content": "retain unknown"},
                                 "SYNTHETIC old permission")["id"]
        self.store.finish(self.store.claim(), "unknown", "ERR_NETWORK")
        queued = self.store.enqueue("get_feed_detail", {"feed_id": "paused-old-read"})["id"]
        self.store.db.execute("UPDATE tasks SET auto_run=1 WHERE id=?", (queued,))
        before_unknown, before_queue = self.store.task(old), self.store.task(queued)
        journal_path = self.config.state_dir / "runtime-state.json"
        journal = json.loads(journal_path.read_text())
        journal["version"] = 1
        items = []
        for number, tool in enumerate(sorted(ops.WRITES)):
            options = {"is_original": False, "tags": [], "products": []} if tool.startswith("publish_") else {}
            run, item = self.reviewed(tool, **options)
            if number == 0:
                self.store.continuous.dispatch("update", {"run_id": run["id"], "plan_ref": "SYNTHETIC four-tool integration only",
                    "plan": {"posts": 2, "comments": 2, "comment_strategy": {"A": 2, "B": 0, "C": 0},
                             "strategy_adjustment_ref": "SYNTHETIC fixture only: comments test identical category contracts"}})
                self.store.continuous.dispatch("schedule", {"run_id": run["id"], "count": 2,
                    "first_at": ops.dt.datetime.fromtimestamp(self.now[0]+7200, ops.dt.timezone.utc).isoformat()})
            if tool == "publish_with_video":
                item["payload"]["schedule_at"] = self.store.continuous.run(run["id"])["body"]["schedule_slots"][1]
                direct_fixtures.content_fixture.register(self.store.continuous, run, [item])
            items.append((run, item, "original-synthetic-%s" % number))
        self.store.continuous.dispatch("review", {"run_id": run["id"], "payloads": [item for _, item, _ in items],
                                                 "evidence": "SYNTHETIC complete four-tool integration fixture"})
        for run, item, ident in items:
            tool = item["tool"]
            sealed, _ = self.store.continuous.authorize(run["id"], tool, item["payload"], "SYNTHETIC permission")
            if tool in {"post_comment_to_feed", "reply_comment_in_feed"}:
                result = {"feed_id": sealed["feed_id"], "success": True}
                if tool == "reply_comment_in_feed":
                    result.update(target_comment_id=sealed.get("comment_id", ""), target_user_id=sealed.get("user_id", ""))
            else:
                result = {"title": sealed["title"], "content": sealed["content"], "status": "发布完成"}
                result["images" if tool == "publish_content" else "video"] = len(sealed["images"]) if tool == "publish_content" else sealed["video"]
            journal["operations"][ident] = {"id": ident, "tool": tool, "status": "succeeded",
                "fingerprint": ops.go_mutation_fingerprint(tool, sealed),
                "updated_at": ops.dt.datetime.fromtimestamp(self.now[0]-3600, ops.dt.timezone.utc).isoformat(), "result": result}
        journal_path.write_text(json.dumps(journal))
        self.start()
        routes = {"post_comment_to_feed": "/api/v1/feeds/comment", "reply_comment_in_feed": "/api/v1/feeds/comment/reply",
                  "publish_content": "/api/v1/publish", "publish_with_video": "/api/v1/publish_video"}
        for run, item, ident in items:
            with self.subTest(tool=item["tool"]):
                arguments = {**item["payload"], "operation_id": ident, "run_id": run["id"], "approval_ref": "SYNTHETIC permission"}
                called = self.call(item["tool"], arguments)
                self.assertFalse(called.get("isError"), called)
                task = self.store.task(ident)
                self.assertEqual((task["status"], task["attempts"], task["auto_run"], task["run_id"]), ("succeeded", 1, 0, run["id"]))
                payload = json.loads(task["payload"])
                self.assertEqual(payload["operation_id"], ident)
                if item["tool"].startswith("publish_"):
                    self.assertIs(payload["is_original"], False)
                    self.assertEqual(payload["tags"], [])
                    self.assertEqual(payload["products"], [])
                    self.assertEqual(payload["schedule_at"], item["payload"]["schedule_at"])
                events = self.store.db.execute("SELECT count(*) FROM events WHERE task_id=?", (ident,)).fetchone()[0]
                request = urllib.request.Request(self.endpoint[:-4] + routes[item["tool"]],
                    data=json.dumps(arguments).encode(), headers={"Content-Type": "application/json", "Idempotency-Key": ident})
                with urllib.request.urlopen(request, timeout=10) as response:
                    replay = json.load(response)
                self.assertTrue(replay["success"], replay)
                self.assertEqual(self.store.task(ident), task)
                self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE task_id=?", (ident,)).fetchone()[0], events)
                changes = [{"content": arguments["content"] + " changed after success"}]
                if item["tool"].startswith("publish_"):
                    changed_schedule = ops.dt.datetime.fromisoformat(arguments["schedule_at"]) + ops.dt.timedelta(hours=1)
                    changes += [{"tags": ["changed-synthetic-topic"]}, {"schedule_at": changed_schedule.isoformat()}]
                else:
                    changes += [{"feed_id": "different-synthetic-target"}]
                self.assert_terminal_conflicts(item["tool"], arguments, changes)
                if item["tool"].startswith("publish_"):
                    original_paths = item["payload"].get("images", [item["payload"].get("video")])
                    sealed_paths = payload.get("images", [payload.get("video")])
                    original_asset = Path(original_paths[0])
                    original_bytes = original_asset.read_bytes()
                    original_asset.write_bytes(b"SYNTHETIC changed bytes under the original media path")
                    self.assert_terminal_conflicts(item["tool"], arguments, [{}])
                    original_asset.write_bytes(original_bytes)
                    for path in set(original_paths + sealed_paths):
                        Path(path).unlink()
                        self.assertFalse(Path(path).exists())
                    # Cleanup can remove original and sealed assets. The exact
                    # original request must still replay; changing that request
                    # under its terminal ID must still report a conflict.
                    stdio_replay = self.call(item["tool"], arguments)
                    self.assertEqual(stdio_replay, called)
                    with urllib.request.urlopen(request, timeout=10) as response:
                        cleaned_replay = json.load(response)
                    self.assertEqual(cleaned_replay, replay)
                    self.assertEqual(self.store.task(ident), task)
                    self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE task_id=?", (ident,)).fetchone()[0], events)
                    self.assert_terminal_conflicts(item["tool"], arguments, changes)
        missing = self.call("post_comment_to_feed", {"feed_id": "unreviewed", "xsec_token": "SYNTHETIC", "content": "never send",
                                                    "operation_id": "must-not-create-task", "approval_ref": "SYNTHETIC permission"})
        self.assertTrue(missing.get("isError"), missing)
        self.assertIn("ERR_QUALITY_REVIEW_REQUIRED", json.dumps(missing))
        self.assertIsNone(self.store.db.execute("SELECT id FROM tasks WHERE id='must-not-create-task'").fetchone())
        self.assertEqual(self.store.task(old), before_unknown)
        self.assertEqual(self.store.task(queued), before_queue)
        self.assertFalse(self.store.status()["runner_enabled"])
        self.assertEqual(self.trap.calls, [], "direct bridge must never call MCP recursively or connect CDP")

    def test_unknown_sql_task_without_go_receipt_never_dispatches_or_changes_attempts(self):
        payload = {"feed_id": "unknown-fixture", "xsec_token": "SYNTHETIC", "content": "retain original uncertain send"}
        ident = self.store.enqueue("post_comment_to_feed", payload, "SYNTHETIC original permission")["id"]
        self.store.finish(self.store.claim(), "unknown", "ERR_NETWORK")
        before = self.store.task(ident)
        events = self.store.db.execute("SELECT count(*) FROM events WHERE task_id=?", (ident,)).fetchone()[0]
        self.start()
        for _ in range(2):
            result = self.call("post_comment_to_feed", {**payload, "operation_id": ident})
            self.assertTrue(result.get("isError"), result)
            self.assertIn("ERR_OPERATION_RECEIPT_REQUIRED", json.dumps(result))
            self.assertEqual(self.store.task(ident), before)
            self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE task_id=?", (ident,)).fetchone()[0], events)
        self.assertFalse(self.store.status()["runner_enabled"])
        self.assertEqual(self.trap.calls, [], "a missing Go receipt must never authorize another send")

    def test_legacy_compact_media_task_without_binding_requires_explicit_receipt_query(self):
        asset = self.root / "legacy-fixture.png"
        asset.write_bytes(b"SYNTHETIC historical media")
        payload = {"title": "Legacy fixture", "content": "SYNTHETIC original copy", "images": [str(asset)],
                   "tags": ["SYNTHETIC original topic"], "schedule_at": "2030-10-20T10:00:00+08:00"}
        ident = self.store.enqueue("publish_content", payload, "SYNTHETIC historical permission")["id"]
        self.store.finish(self.store.claim(), "succeeded", result={"fixture": "SYNTHETIC old compact result"})
        journal_path = self.config.state_dir / "runtime-state.json"
        journal = json.loads(journal_path.read_text())
        journal["version"] = 1
        journal["operations"][ident] = {"id": ident, "tool": "publish_content", "status": "succeeded",
            "fingerprint": ops.go_mutation_fingerprint("publish_content", payload),
            "updated_at": ops.dt.datetime.fromtimestamp(self.now[0]-3600, ops.dt.timezone.utc).isoformat(),
            "result": {"title": payload["title"], "content": payload["content"], "images": 1, "status": "发布完成"}}
        journal_path.write_text(json.dumps(journal))
        # Historical retention may have kept only a small target tombstone;
        # unlike a new task, this record has no original request binding.
        self.store.db.execute("UPDATE tasks SET payload=?,request_binding=NULL WHERE id=?", (json.dumps({"title": payload["title"]}), ident))
        asset.unlink()
        before = self.store.task(ident)
        events = self.store.db.execute("SELECT count(*) FROM events WHERE task_id=?", (ident,)).fetchone()[0]
        self.start()
        result = self.call("publish_content", {**payload, "operation_id": ident})
        self.assertTrue(result.get("isError"), result)
        self.assertIn("ERR_RECEIPT_QUERY_REQUIRED", json.dumps(result))
        self.assertEqual(self.store.task(ident), before)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE task_id=?", (ident,)).fetchone()[0], events)
        self.assertEqual(self.trap.calls, [])

    def test_manual_future_deadline_and_unfinished_dependency_defer_without_attempt_or_io(self):
        run, item = self.reviewed()
        due = self.now[0] + 3600
        ident = self.store.enqueue(item["tool"], item["payload"], "SYNTHETIC manual permission",
                                   run_id=run["id"], not_before=due)["id"]
        parent = self.store.enqueue("get_feed_detail", {"feed_id": "dependency-fixture"})["id"]
        arguments = {**item["payload"], "operation_id": ident, "run_id": run["id"], "approval_ref": "SYNTHETIC permission"}
        self.start()
        before = self.store.task(ident)
        events = self.store.db.execute("SELECT count(*) FROM events WHERE task_id=?", (ident,)).fetchone()[0]
        result = self.call(item["tool"], arguments)
        self.assertTrue(result.get("isError"), result)
        text = next(part["text"] for part in result["content"] if part.get("type") == "text")
        self.assertIn("ERR_RATE_LIMIT", text)
        self.assertIn("operation_id=" + ident, text)
        retry = re.search(r"retry_at=([^\s；]+)", text)
        self.assertIsNotNone(retry, text)
        self.assertAlmostEqual(ops.parse_timestamp(retry.group(1)), due, delta=0.001)
        self.assertEqual(self.store.task(ident), before)
        self.store.db.execute("UPDATE tasks SET next_at=0,dependency=? WHERE id=?", (parent, ident))
        before = self.store.task(ident)
        parent_before = self.store.task(parent)
        result = self.call(item["tool"], arguments)
        self.assertTrue(result.get("isError"), result)
        text = next(part["text"] for part in result["content"] if part.get("type") == "text")
        self.assertIn("ERR_DEPENDENCY_PENDING", text)
        self.assertIn("dependency_id=" + parent, text)
        self.assertIn("dependency_status=queued", text)
        self.assertEqual(self.store.task(ident), before)
        self.assertEqual(self.store.task(parent), parent_before)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE task_id=?", (ident,)).fetchone()[0], events)
        self.assertEqual(self.store.task(ident)["attempts"], 0)
        self.assertFalse(self.store.status()["runner_enabled"])
        self.assertEqual(self.trap.calls, [], "deferred manual work must not enter the browser or call MCP recursively")

    def test_writes_disabled_fails_before_creating_any_task_or_browser_connection(self):
        self.config.data["allow_write"] = False
        self.start()
        result = self.call("post_comment_to_feed", {"feed_id": "synthetic", "xsec_token": "SYNTHETIC", "content": "never send"})
        self.assertTrue(result.get("isError"), result)
        self.assertIn("ERR_WRITE_NOT_ALLOWED", json.dumps(result))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM tasks").fetchone()[0], 0)
        self.assertFalse(self.store.status()["runner_enabled"])
        self.assertEqual(self.trap.calls, [])


if __name__ == "__main__":
    unittest.main()
