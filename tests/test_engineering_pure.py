"""Normal local queue/configuration checks; no fault injection or site access."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("engineering_ops", ROOT / "runtime/xhs_ops.py")
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)


class EngineeringPureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.path = base / "private.json"
        data = json.loads((ROOT / "config/xhs.example.json").read_text())
        data.update(state_dir=str(base / "state"), output_dir=str(base / "output"))
        self.path.write_text(json.dumps(data))
        ops.prepare_state(self.path)
        self.config = ops.Config(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_task_submission_and_view(self):
        value = ops.bridge(self.config, dict(action="submit", tool="get_runtime_status", payload={}, auto_run=False))
        view = ops.bridge(self.config, dict(action="task", task_id=value["id"]))
        self.assertEqual(view["status"], "queued")
        self.assertNotIn("payload", view)
        self.assertFalse(value["runner_enabled"])

    def test_old_manual_queue_does_not_opt_into_background(self):
        store = ops.Store(self.config)
        try:
            value = store.enqueue("get_runtime_status", {})
            self.assertIsNone(store.claim(automatic=True))
            self.assertEqual(store.task(value["id"])["status"], "queued")
        finally:
            store.db.close()

    def test_opt_in_and_pause_persist(self):
        value = ops.bridge(self.config, dict(action="submit", tool="get_runtime_status", payload={}, auto_run=True, not_before=4102444800))
        self.assertTrue(value["runner_enabled"])
        paused = ops.bridge(self.config, dict(action="control", enabled=False))
        self.assertFalse(paused["runner_enabled"])
        self.assertEqual(ops.bridge(self.config, dict(action="task", task_id=value["id"]))["next_at"], 4102444800)

    def test_platform_rate_limit_holds_task_without_retrying_or_blocking_other_work(self):
        class Limited:
            def call(self, tool, payload):
                raise ops.ToolFailure("ERR_RATE_LIMITED")
        store = ops.Store(self.config)
        try:
            item = store.enqueue("search_feeds", {"keyword": "fixture"})
            result = ops.tick(store, Limited())
            task = store.task(item["id"])
            self.assertEqual(task["status"], "blocked")
            self.assertEqual(task["attempts"], 1)
            self.assertIsNone(store.claim())
            self.assertFalse(store.status()["account_blocked"])
            other = store.enqueue("get_runtime_status", {})
            self.assertEqual(store.claim()["id"], other["id"])
        finally:
            store.db.close()

    def write_deadlines(self, **values):
        path = self.config.state_dir / "runtime-state.json"
        data = json.loads(path.read_text())
        data.update({key: ops.dt.datetime.fromtimestamp(value, ops.dt.timezone.utc).isoformat() for key, value in values.items()})
        path.write_text(json.dumps(data))

    def test_search_wait_is_local_persistent_and_automatically_due(self):
        now = [1800000000.0]
        self.write_deadlines(next_search_at=now[0] + 60)
        store = ops.Store(self.config, now=lambda: now[0])
        calls = []
        class Normal:
            def call(self, tool, payload):
                calls.append(tool)
                return {"read_at": "fixture"}
        try:
            item = store.enqueue("search_feeds", {"keyword": "fixture"})
            store.db.execute("UPDATE tasks SET auto_run=1 WHERE id=?", (item["id"],))
            for _ in range(5):
                ops.tick(store, Normal(), automatic=True)
            self.assertEqual(calls, [])
            self.assertEqual(store.task(item["id"])["attempts"], 0)
            self.assertEqual(store.task_view(item["id"])["next_at"], now[0] + 60)
            store.db.close()
            store = ops.Store(self.config, now=lambda: now[0])
            now[0] += 60
            ops.tick(store, Normal(), automatic=True)
            self.assertEqual(calls, ["search_feeds"])
            self.assertEqual(store.task(item["id"])["status"], "succeeded")
        finally:
            store.db.close()

    def test_publication_wait_does_not_delay_due_comment(self):
        now = [1800000000.0]
        self.config.allow_write = True
        self.write_deadlines(next_publish_at=now[0] + 5400, next_write_at=now[0] + 180)
        store = ops.Store(self.config, now=lambda: now[0])
        calls = []
        class Normal:
            def call(self, tool, payload):
                calls.append((tool, payload["operation_id"]))
                return {"verified": True}
        image = Path(self.tmp.name) / "fixture.png"
        image.write_bytes(b"local-only-fixture")
        try:
            post = store.enqueue("publish_content", {"title": "fixture", "content": "fixture", "images": [str(image)], "schedule_at": "2030-10-20T10:00:00+08:00"}, "fixture-approval")
            comment = store.enqueue("post_comment_to_feed", {"feed_id": "fixture", "content": "fixture"}, "fixture-approval")
            self.assertEqual(store.task_view(post["id"])["next_at"], now[0] + 5400)
            ops.tick(store, Normal())
            self.assertEqual(calls, [])
            now[0] += 180
            ops.tick(store, Normal())
            self.assertEqual(calls, [("post_comment_to_feed", comment["id"])])
            self.assertEqual(store.task(post["id"])["attempts"], 0)
            now[0] += 5220
            ops.tick(store, Normal())
            self.assertEqual(calls[-1], ("publish_content", post["id"]))
            self.assertEqual(store.task(post["id"])["status"], "succeeded")
        finally:
            store.db.close()

    def test_local_deferrals_do_not_exhaust_retries_or_add_write_wait(self):
        now = [1800000000.0]
        self.config.allow_write = True
        store = ops.Store(self.config, now=lambda: now[0])
        class Deferred:
            def call(self, tool, payload):
                raise ops.ToolFailure("ERR_RATE_LIMIT", payload["operation_id"], now[0] + 7)
        class Normal:
            def call(self, tool, payload):
                return {"verified": True}
        try:
            item = store.enqueue("post_comment_to_feed", {"feed_id": "fixture", "content": "fixture"}, "fixture-approval")
            for _ in range(5):
                ops.tick(store, Deferred())
                view = store.task_view(item["id"])
                self.assertEqual(view["status"], "queued")
                self.assertEqual(view["attempts"], 0)
                self.assertEqual(view["next_at"], now[0] + 7)
                now[0] += 7
            ops.tick(store, Normal())
            self.assertEqual(store.task(item["id"])["status"], "succeeded")
            self.assertEqual(store.task(item["id"])["attempts"], 1)
        finally:
            store.db.close()

    def test_retry_timestamp_keeps_fractional_seconds(self):
        transport = ops.MCPTransport(self.config)
        result = {"isError": True, "content": [{"type": "text", "text": "ERR_RATE_LIMIT: scope=search retry_at=2027-01-15T08:00:00.123456789+08:00"}]}
        with patch.object(transport, "connect"), patch.object(transport, "request", return_value=result):
            with self.assertRaises(ops.ToolFailure) as failure:
                transport.call("search_feeds", {"keyword": "fixture"})
        expected = ops.dt.datetime.fromisoformat("2027-01-15T08:00:00.123456+08:00").timestamp()
        self.assertAlmostEqual(failure.exception.retry_at, expected, places=5)

    def test_search_task_reuses_recent_snapshot_then_keeps_new_read_history(self):
        now = [1800000000.0]
        store = ops.Store(self.config, now=lambda: now[0])
        class Normal:
            def call(self, tool, payload):
                return {"read_at": now[0]}
        try:
            first = store.enqueue("search_feeds", {"keyword": "fixture"})
            ops.tick(store, Normal())
            repeated = store.enqueue("search_feeds", {"keyword": " fixture "})
            self.assertTrue(repeated["duplicate"])
            self.assertEqual(repeated["id"], first["id"])
            now[0] += 900
            later = store.enqueue("search_feeds", {"keyword": "fixture"})
            self.assertFalse(later["duplicate"])
            self.assertNotEqual(later["id"], first["id"])
            self.assertEqual(store.task(first["id"])["status"], "succeeded")
            self.assertEqual(store.enqueue("search_feeds", {"keyword": "fixture"})["id"], later["id"])
            ops.tick(store, Normal())
            fresh = store.enqueue("search_feeds", {"keyword": "fixture", "fresh": True})
            ops.tick(store, Normal())
            self.assertNotEqual(store.enqueue("search_feeds", {"keyword": "fixture", "fresh": True})["id"], fresh["id"])
        finally:
            store.db.close()

    def test_detail_snapshots_expire_and_fresh_diagnostics_are_not_permanent(self):
        now = [1800000000.0]
        store = ops.Store(self.config, now=lambda: now[0])
        class Normal:
            def call(self, tool, payload): return {"read_at": now[0]}
        try:
            payload = {"feed_id": "fixture", "xsec_token": "first"}
            first = store.enqueue("get_feed_detail", payload)
            ops.tick(store, Normal())
            payload["xsec_token"] = "rotated"
            self.assertEqual(store.enqueue("get_feed_detail", payload)["id"], first["id"])
            now[0] += 900
            self.assertNotEqual(store.enqueue("get_feed_detail", payload)["id"], first["id"])
            diagnostic = store.enqueue("get_runtime_status", {})
            ops.tick(store, Normal())
            ops.tick(store, Normal())
            self.assertNotEqual(store.enqueue("get_runtime_status", {})["id"], diagnostic["id"])
        finally: store.db.close()

    def test_shared_read_wait_is_visible_to_next_agent_and_leaves_diagnostics_due(self):
        now = [1800000000.0]
        self.write_deadlines(next_read_at=now[0] + 10)
        store = ops.Store(self.config, now=lambda: now[0])
        try:
            detail = store.enqueue("get_feed_detail", {"feed_id": "fixture", "xsec_token": "private-value"})
            diagnostic = store.enqueue("get_runtime_status", {})
            claimed = store.claim()
            self.assertEqual(claimed["id"], diagnostic["id"])
            pending = next(t for t in store.status()["pending_tasks"] if t["id"] == detail["id"])
            self.assertEqual(pending["next_at"], now[0] + 10)
            self.assertNotIn("payload", pending)
            self.assertNotIn("private-value", json.dumps(pending))
            store.finish(claimed, "succeeded", result={})
            now[0] += 10
            self.assertEqual(store.claim()["id"], detail["id"])
        finally: store.db.close()

if __name__ == "__main__":
    unittest.main()
