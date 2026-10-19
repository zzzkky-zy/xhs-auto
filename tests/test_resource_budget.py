"""Resource invariants on temporary local state; no browser/platform actions."""
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
import xhs_ops as ops
spec = importlib.util.spec_from_file_location("cloud_host", ROOT / "scripts/cloud_host.py")
cloud = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cloud)


class ResourceBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "private.json"
        self.path.write_text(json.dumps({"schema_version": 1, "account_id": "fixture", "state_dir": "state",
            "output_dir": "output", "mcp_url": "http://127.0.0.1:1/mcp", "cdp_url": "http://127.0.0.1:1"}))
        ops.prepare_state(self.path)
        self.config = ops.Config(self.path)
        self.now = [1800000000.0]
        self.s = ops.Store(self.config, now=lambda: self.now[0])

    def tearDown(self):
        self.s.db.close()
        self.temp.cleanup()

    def completed(self, tool, payload, result=None, dependency=None):
        task = self.s.enqueue(tool, payload, dependency=dependency)
        self.s.db.execute("UPDATE tasks SET status='succeeded',result=?,completed_at=? WHERE id=?",
            (json.dumps(result or {"large": "x"*8192}), self.now[0], task["id"]))
        return task["id"]

    def test_legacy_migration_keeps_receipts_and_starts_retention_clock_once(self):
        task = self.completed("post_comment_to_feed", {"feed_id": "f", "content": "keep"})
        # Recreate the immediately prior schema without changing identities.
        self.s.db.execute("DROP INDEX tasks_completed")
        self.s.db.execute("ALTER TABLE tasks DROP COLUMN completed_at")
        self.s.db.close()
        self.s = ops.Store(self.config, now=lambda: self.now[0])
        self.assertEqual(self.s.task(task)["completed_at"], self.now[0])
        self.now[0] += 3600
        self.s.db.close()
        self.s = ops.Store(self.config, now=lambda: self.now[0])
        self.assertEqual(self.s.task(task)["completed_at"], self.now[0]-3600)
        self.assertEqual(self.s.enqueue("post_comment_to_feed", {"feed_id": "f", "content": "keep"})["id"], task)

    def test_expired_read_details_removed_dependency_kept_and_live_work_untouched(self):
        old = self.completed("search_feeds", {"keyword": "old"})
        parent = self.completed("get_feed_detail", {"feed_id": "parent"})
        child = self.s.enqueue("get_feed_detail", {"feed_id": "child"}, dependency=parent)["id"]
        unknown = self.s.enqueue("post_comment_to_feed", {"feed_id": "u", "content": "unknown"})["id"]
        self.s.db.execute("UPDATE tasks SET status='unknown',completed_at=? WHERE id=?", (self.now[0], unknown))
        self.now[0] += 25*3600
        self.s.continuous.maintain()
        with self.assertRaises(ValueError): self.s.task(old)
        self.assertEqual(json.loads(self.s.task(parent)["result"]), {"archived": True})
        self.assertEqual(self.s.task(child)["status"], "queued")
        self.assertEqual(json.loads(self.s.task(unknown)["payload"])["content"], "unknown")

    def test_old_successful_write_loses_large_details_but_still_deduplicates(self):
        payload = {"feed_id": "f", "content": "original", "xsec_token": "private"}
        task = self.completed("post_comment_to_feed", payload)
        self.now[0] += 8*86400
        self.s.continuous.maintain()
        self.assertEqual(json.loads(self.s.task(task)["payload"]), {"feed_id": "f"})
        self.assertEqual(self.s.enqueue("post_comment_to_feed", payload)["id"], task)
        self.assertEqual(self.s.task(task)["status"], "succeeded")

    def test_pending_acceptance_and_unknown_batch_sources_never_expire(self):
        run_id = "1"*32
        self.s.continuous  # Batch tables are intentionally initialized lazily.
        self.s.db.execute("INSERT INTO ops_runs VALUES (?,?,?,?,?)", (run_id, "awaiting_acceptance", self.now[0], self.now[0], "{}"))
        task = self.completed("search_feeds", {"keyword": "evidence"})
        self.s.db.execute("UPDATE tasks SET run_id=? WHERE id=?", (run_id, task))
        self.now[0] += 90*86400
        self.s.continuous.maintain()
        self.assertIn("large", json.loads(self.s.task(task)["result"]))

    def test_low_space_does_not_claim_or_spend_attempts_and_recovers_without_resume(self):
        task = self.s.enqueue("search_feeds", {"keyword": "pending"})["id"]
        with mock.patch.object(ops.shutil, "disk_usage", return_value=mock.Mock(free=1024)):
            self.assertFalse(self.s.resources()["admission_allowed"])
            self.assertIsNone(self.s.claim())
            self.assertEqual(self.s.task(task)["attempts"], 0)
            with self.assertRaisesRegex(ValueError, "ERR_RESOURCE_BUDGET"):
                self.s.enqueue("search_feeds", {"keyword": "new"})
        self.assertEqual(self.s.claim()["id"], task)

    def test_state_budget_and_inode_reserve_are_separate_from_assets(self):
        with (self.config.state_dir / "bounded-test-data").open("wb") as f: f.truncate(513*1024**2)
        self.assertEqual(self.s.resources()["pressure"], "state_budget")
        (self.config.state_dir / "bounded-test-data").unlink()
        with mock.patch.object(ops.os, "statvfs", return_value=mock.Mock(f_files=10000, f_favail=10)), \
             mock.patch.object(ops.shutil, "disk_usage", return_value=mock.Mock(free=10*1024**3)):
            self.assertEqual(self.s.resources()["pressure"], "free_inode_reserve")

    def test_background_under_disk_pressure_never_probes_the_browser(self):
        task = self.s.enqueue("search_feeds", {"keyword": "wait"})["id"]
        self.s.db.execute("UPDATE tasks SET auto_run=1 WHERE id=?", (task,))
        self.s.db.execute("INSERT OR REPLACE INTO meta VALUES ('runner_enabled','1')")
        with mock.patch.object(ops.shutil, "disk_usage", return_value=mock.Mock(free=1024)), \
             mock.patch.object(ops, "recover_pending", side_effect=AssertionError("resource recovery probe")), \
             mock.patch.object(ops.MCPTransport, "call", side_effect=AssertionError("site request")):
            result = ops.bridge(self.config, {"action": "advance"})
        self.assertEqual(result["resources"]["pressure"], "free_disk_reserve")
        self.assertEqual(self.s.task(task)["status"], "queued")
        self.assertEqual(self.s.task(task)["attempts"], 0)

    def test_idle_sleeps_until_maintenance_and_future_task_keeps_exact_deadline(self):
        self.s.continuous.maintain()
        self.assertEqual(self.s.status()["background_next_at"], self.now[0]+3600)
        task = self.s.enqueue("get_runtime_status", {}, not_before=self.now[0]+123)
        self.s.db.execute("UPDATE tasks SET auto_run=1 WHERE id=?", (task["id"],))
        self.s.db.execute("INSERT OR REPLACE INTO meta VALUES ('runner_enabled','1')")
        self.assertEqual(self.s.status()["background_next_at"], self.now[0]+123)
        self.s.db.execute("UPDATE tasks SET status='blocked',error_code='ERR_RATE_LIMITED' WHERE id=?", (task["id"],))
        self.assertEqual(self.s.status()["background_next_at"], self.now[0]+3600)  # A held platform limit needs no minute polling.

    def test_paused_advance_runs_local_cleanup_without_browser_or_recovery(self):
        self.completed("search_feeds", {"keyword": "expired"})
        self.now[0] += 2*86400
        with mock.patch.object(ops, "recover_pending", side_effect=AssertionError("paused recovery")), \
             mock.patch.object(ops.MCPTransport, "call", side_effect=AssertionError("site request")), \
             mock.patch.object(ops.time, "time", return_value=self.now[0]):
            # Store receives its own clock below; bridge uses the normal store.
            original = ops.Store
            with mock.patch.object(ops, "Store", side_effect=lambda c: original(c, now=lambda: self.now[0])):
                result = ops.bridge(self.config, {"action": "advance"})
        self.assertFalse(result["runner_enabled"])
        self.assertFalse(result["counts"].get("succeeded"))

    def test_cloud_profile_preserves_account_paths_permissions_and_schedule(self):
        before = dict(self.config.data)
        cloud.apply_profile(self.config)
        after = json.loads(self.path.read_text())
        for key in before:
            if key not in {"resources", "retention"}: self.assertEqual(after[key], before[key])
        self.assertEqual(ops.Config(self.path).runtime_env()["GOMEMLIMIT"], "256MiB")
        self.assertEqual(ops.Config(self.path).runtime_env()["GOMAXPROCS"], "2")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_generated_limits_include_browser_only_when_host_assigns_it(self):
        files = cloud.unit_files(self.config, "host-browser.service")
        self.assertIn("MemoryMax=3G", files["xhs-operations.slice"])
        self.assertIn("MemoryMax=512M", files["xhs-operations.service"])
        self.assertIn("MemoryMax=2560M", files["host-browser.service.d/xhs-resources.conf"])
        self.assertIn("Slice=xhs-operations.slice", files["host-browser.service.d/xhs-resources.conf"])
        with self.assertRaises(ValueError): cloud.unit_files(self.config, "../foreign.service")
        self.assertEqual(cloud.quote_unit('/a b/100%"x'), '"/a b/100%%\\"x"')

    def test_large_log_line_is_discarded_whole_without_leaking_split_credentials(self):
        data = b"safe\n"+b"x"*65530+b"xsec_token=private-value"+b"y"*70000+b"\nnext\n"
        lines = list(ops.bounded_log_lines(io.BytesIO(data)))
        self.assertEqual(lines, [b"safe\n", b"ERR_LOG_LINE_TOO_LARGE: content omitted\n", b"next\n"])
        self.assertNotIn(b"private-value", b"".join(lines))

    def test_180_simulated_days_reuse_sqlite_pages_instead_of_accumulating_read_blobs(self):
        sizes = []
        for day in range(180):
            with self.s.transaction():
                for number in range(12):
                    ident = f"r-{day}-{number}"
                    self.s.db.execute("INSERT INTO tasks(id,account,tool,fingerprint,payload,status,result,completed_at) VALUES (?,?,?,?,?,?,?,?)",
                        (ident, "fixture", "search_feeds", ident, "{}", "succeeded", json.dumps({"blob": "x"*16384}), self.now[0]))
                ident = f"w-{day}"
                self.s.db.execute("INSERT INTO tasks(id,account,tool,fingerprint,payload,status,result,completed_at) VALUES (?,?,?,?,?,?,?,?)",
                    (ident, "fixture", "post_comment_to_feed", ident, '{"feed_id":"f","content":"detail"}', "succeeded", '{"ok":true}', self.now[0]))
            self.s.continuous.maintain()
            sizes.append(self.s.path.stat().st_size)
            self.now[0] += 86400
        self.assertLess(self.s.db.execute("SELECT count(*) FROM tasks WHERE tool='search_feeds'").fetchone()[0], 40)
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM tasks WHERE tool='post_comment_to_feed'").fetchone()[0], 180)
        self.assertLess(max(sizes[30:]), 3*1024**2)  # 35MiB of cumulative read responses, not retained.
        self.assertLess(max(sizes[-30:])-max(sizes[30:60]), 1024**2)  # only small durable receipts grow.
        self.long_run_report = {"simulated_days": 180, "real_elapsed_days": 0,
            "read_responses_created": 180*12, "cumulative_read_body_bytes": 180*12*16384,
            "peak_sqlite_bytes": max(sizes), "final_sqlite_bytes": sizes[-1],
            "retained_read_tasks": self.s.db.execute("SELECT count(*) FROM tasks WHERE tool='search_feeds'").fetchone()[0],
            "retained_write_receipts": 180, "real_site_actions": 0}


if __name__ == "__main__": unittest.main()
