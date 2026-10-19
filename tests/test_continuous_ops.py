"""Lifecycle/retention checks use temporary files only, without platform access."""
import importlib.util
import contextlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import content_fixture

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("continuous_test_ops", ROOT / "runtime/xhs_ops.py")
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)


class ContinuousTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        path = self.base / "private.json"
        path.write_text(json.dumps({"schema_version": 1, "account_id": "fixture", "state_dir": "state", "output_dir": "output",
                                    "mcp_url": "http://127.0.0.1:1/mcp", "expected_user_id": "fixture-user", "allow_write": True}))
        ops.prepare_state(path)
        self.config = ops.Config(path)
        self.now = [1800000000.0]
        self.s = ops.Store(self.config, now=lambda: self.now[0])
        self.c = self.s.continuous
        self.c.dispatch("configure", {"authorization_ref": "fixture actual request", "quality_standard": "fixture complete content standard",
                                       "allowed_tools": ["publish_content", "post_comment_to_feed"],
                                       "content_profile": content_fixture.profile(self.base)})

    def tearDown(self):
        self.s.db.close()
        self.tmp.cleanup()

    def begin(self, **plan):
        plan = plan or {"posts": 1, "comments": 1}
        if plan.get("comments"):
            count = plan["comments"]
            plan.setdefault("comment_strategy", {"A": (count+1)//2, "B": count//2, "C": 0})
        return self.c.dispatch("begin", {"plan_ref": "fixture flexible plan", "plan": plan})

    def payloads(self, run):
        image = self.c.directory(run["id"]) / "final" / "image.png"
        image.write_bytes(b"fixture reviewed image")
        previous = self.s.db.execute("SELECT max(at) FROM ops_calendar").fetchone()[0] or 0
        slots = self.c.dispatch("schedule", {
            "run_id": run["id"], "count": 3, "first_at": ops.dt.datetime.fromtimestamp(max(self.now[0]+7200, previous+3600), ops.dt.timezone.utc).isoformat()})
        post = {"tool": "publish_content", "payload": {"title": "fixture", "content": "fixture useful copy", "images": [str(image)], "schedule_at": slots["schedule_slots"][0]}}
        comment = {"tool": "post_comment_to_feed", "payload": {"feed_id": "fixture", "content": "fixture specific suggestion", "xsec_token": "private-fixture"}}
        return [post, comment]

    def review(self, run, payloads):
        content_fixture.register(self.c, run, payloads)
        return self.c.dispatch("review", {"run_id": run["id"], "payloads": payloads,
                                           "evidence": "SYNTHETIC TEST ONLY: content contract, not an actual visual or semantic assertion"})

    def complete(self, run, items, manual="fixture real live-test permission"):
        ids = []
        for item in items:
            value = self.s.enqueue(item["tool"], item["payload"], manual, run_id=run["id"])
            self.s.db.execute("UPDATE tasks SET status='succeeded',result=? WHERE id=?", (json.dumps({"verified": True, "private_source": "large fixture source"}), value["id"]))
            ids.append(value["id"])
        return ids

    def activate(self):
        run = self.begin()
        items = self.payloads(run)
        self.review(run, items)
        self.complete(run, items)
        self.c.dispatch("finish", {"run_id": run["id"]})
        self.c.dispatch("accept", {"run_id": run["id"], "human_acceptance_ref": "fixture human explicitly accepted complete first batch"})

    def test_first_batch_cannot_self_approve_and_acceptance_needs_complete_chain(self):
        run = self.begin()
        items = self.payloads(run)
        self.review(run, items)
        with self.assertRaisesRegex(ValueError, "ERR_APPROVAL_REQUIRED"):
            self.s.enqueue(items[0]["tool"], items[0]["payload"], run_id=run["id"])
        with self.assertRaisesRegex(ValueError, "ERR_HUMAN_ACCEPTANCE_REQUIRED"):
            self.c.dispatch("accept", {"run_id": run["id"]})
        self.complete(run, items[:1])
        self.c.dispatch("finish", {"run_id": run["id"]})
        with self.assertRaisesRegex(ValueError, "ERR_VALIDATION_INCOMPLETE"):
            self.c.dispatch("accept", {"run_id": run["id"], "human_acceptance_ref": "fixture incomplete acceptance"})

    def test_active_scope_addition_requires_new_authorization_and_preserves_run(self):
        run = self.begin(posts=2, comments=10)
        before = self.c.run(run["id"])
        tools = ["publish_content", "post_comment_to_feed", "reply_comment_in_feed"]
        with self.assertRaisesRegex(ValueError, "ERR_AUTHORIZATION_REQUIRED"):
            self.c.dispatch("configure", {"allowed_tools": tools})
        self.c.dispatch("configure", {"allowed_tools": tools, "authorization_ref": "fixture user explicitly requests comment replies"})
        after = self.c.run(run["id"])
        self.assertEqual(before["id"], after["id"])
        self.assertEqual(before["body"]["plan"], after["body"]["plan"])
        self.assertEqual(self.c.policy()["allowed_tools"], sorted(tools))
        with self.assertRaisesRegex(ValueError, "ERR_RUN_ACTIVE"):
            self.c.dispatch("configure", {"allowed_tools": ["reply_comment_in_feed"]})

    def test_one_acceptance_allows_later_agent_review_without_new_human_review(self):
        self.activate()
        run = self.begin(posts=2, comments=7, topics=["new user/agent plan"])
        self.assertEqual(run["body"]["kind"], "autonomous")
        items = self.payloads(run)
        items[0]["payload"]["title"] = "different later batch"
        self.review(run, items)
        item = self.s.enqueue(items[0]["tool"], items[0]["payload"], run_id=run["id"])
        task = self.s.task(item["id"])
        self.assertTrue(task["approval_ref"].startswith("continuous:"))
        fixed = Path(json.loads(task["payload"])["images"][0])
        Path(items[0]["payload"]["images"][0]).write_bytes(b"later draft edits")
        self.assertEqual(fixed.read_bytes(), b"fixture reviewed image")

    def test_copy_or_image_change_requires_agent_review(self):
        run = self.begin()
        items = self.payloads(run)
        self.review(run, items)
        items[0]["payload"]["content"] += " changed"
        with self.assertRaisesRegex(ValueError, "ERR_QUALITY_REVIEW_REQUIRED"):
            self.s.enqueue(items[0]["tool"], items[0]["payload"], "fixture permission", run_id=run["id"])

    def test_fixed_asset_is_checked_again_before_claim(self):
        run = self.begin()
        items = self.payloads(run)
        self.review(run, items)
        task = self.s.enqueue(items[0]["tool"], items[0]["payload"], "fixture permission", run_id=run["id"])
        fixed = Path(json.loads(self.s.task(task["id"])["payload"])["images"][0])
        fixed.chmod(0o600)
        fixed.write_bytes(b"fixture corrupted after enqueue")
        self.assertIsNone(self.s.claim())
        self.assertEqual(self.s.task(task["id"])["error_code"], "ERR_ASSET_CHANGED")

    def test_schedule_draws_once_survives_handoff_and_stays_in_range(self):
        run = self.begin()
        self.payloads(run)
        slots = self.c.dispatch("schedule", {"run_id": run["id"], "count": 99, "first_at": "not used"})["schedule_slots"]
        restarted = ops.Store(self.config, now=lambda: self.now[0])
        try:
            again = restarted.continuous.dispatch("schedule", {"run_id": run["id"]})["schedule_slots"]
            self.assertEqual(slots, again)
            stamps = [ops.parse_timestamp(s) for s in slots]
            self.assertTrue(all(3600 <= b-a <= 5400 for a, b in zip(stamps, stamps[1:])))
            self.assertEqual(restarted.continuous.dispatch("begin", {"plan": {}})["id"], run["id"])
        finally:
            restarted.db.close()

    def test_batch_search_refreshes_without_replacing_old_evidence(self):
        run = self.begin()
        task = self.s.enqueue("search_feeds", {"keyword": "fixture"}, run_id=run["id"])
        self.assertIs(json.loads(self.s.task(task["id"])["payload"])["fresh"], True)

    def test_accepted_complete_batch_cleans_assets_keeps_dedup_and_leaves_other_files(self):
        unrelated = self.config.output_dir / "user-file.png"
        unrelated.write_bytes(b"keep")
        run = self.begin()
        items = self.payloads(run)
        self.review(run, items)
        ids = self.complete(run, items)
        self.c.dispatch("finish", {"run_id": run["id"]})
        self.assertTrue(self.c.directory(run["id"]).exists())  # first human still needs to inspect images
        self.c.dispatch("accept", {"run_id": run["id"], "human_acceptance_ref": "fixture real human acceptance"})
        self.assertFalse(self.c.directory(run["id"]).exists())
        self.assertEqual(unrelated.read_bytes(), b"keep")
        duplicate = self.s.enqueue(items[1]["tool"], items[1]["payload"], "fixture permission")
        self.assertEqual(duplicate["id"], ids[1])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(json.loads(self.s.task(ids[1])["payload"]), {"feed_id": "fixture"})

    def test_unknown_or_pending_work_is_never_cleaned(self):
        run = self.begin()
        task = self.s.enqueue("get_runtime_status", {}, run_id=run["id"])
        self.s.db.execute("UPDATE tasks SET status='unknown' WHERE id=?", (task["id"],))
        with self.assertRaisesRegex(ValueError, "ERR_RUN_UNRESOLVED"):
            self.c.dispatch("finish", {"run_id": run["id"], "abandon": True})
        self.now[0] += 1000000
        self.c.dispatch("maintain", {})
        self.assertTrue(self.c.directory(run["id"]).exists())

    def test_abandoned_cleanup_after_deadline_and_symlink_never_follows_user_data(self):
        run = self.begin()
        self.c.dispatch("finish", {"run_id": run["id"], "abandon": True})
        self.assertTrue(self.c.directory(run["id"]).exists())
        self.now[0] += 73*3600
        self.c.dispatch("maintain", {})
        self.assertFalse(self.c.directory(run["id"]).exists())
        other = self.begin()
        path = self.c.directory(other["id"])
        shutil = __import__("shutil")
        shutil.rmtree(path)
        target = self.base / "unrelated"
        target.mkdir()
        path.symlink_to(target, target_is_directory=True)
        self.c.dispatch("finish", {"run_id": other["id"], "abandon": True})
        self.now[0] += 73*3600
        with self.assertRaisesRegex(ValueError, "ERR_RUN_PATH"):
            self.c.dispatch("maintain", {})
        self.assertTrue(target.exists())

    def test_random_write_ranges_and_validation(self):
        values = [self.config.draw_write_interval() for _ in range(100)]
        self.assertTrue(all(60 <= n <= 180 for n in values))
        self.assertGreater(len(set(values)), 1)

    def test_plan_can_change_and_only_queued_tasks_can_be_cancelled(self):
        run = self.begin()
        changed = self.c.dispatch("update", {"run_id": run["id"], "plan_ref": "fixture later agreement", "plan": {"comments": 2}})
        self.assertEqual(changed["body"]["plan"]["comments"], 2)
        task = self.s.enqueue("get_runtime_status", {}, run_id=run["id"])
        self.c.dispatch("cancel", {"run_id": run["id"], "task_id": task["id"]})
        self.assertEqual(self.s.task(task["id"])["status"], "cancelled")
        with self.assertRaisesRegex(ValueError, "ERR_CANCEL_NOT_QUEUED"):
            self.c.dispatch("cancel", {"run_id": run["id"], "task_id": task["id"]})

    def test_resource_budget_stops_new_work_without_deleting_unknown_evidence(self):
        run = self.begin()
        self.s.db.execute("UPDATE ops_runs SET state='completed',body='{}' WHERE id=?", (run["id"],))
        # An active unknown task prevents cleanup even if metadata is terminal.
        self.s.db.execute("INSERT INTO tasks(id,account,tool,fingerprint,payload,status,run_id) VALUES ('unknown','fixture','publish_content','unknown','{}','unknown',?)", (run["id"],))
        large = self.c.directory(run["id"]) / "work/needed-evidence"
        with large.open("wb") as f:
            f.truncate(256*1024*1024)
        with self.assertRaisesRegex(ValueError, "ERR_RESOURCE_BUDGET"):
            self.begin()
        self.assertTrue(large.exists())

    def test_claim_winning_cancel_race_is_never_reported_as_cancelled(self):
        run = self.begin()
        task = self.s.enqueue("get_runtime_status", {}, run_id=run["id"])
        other = ops.Store(self.config, now=lambda: self.now[0])
        original = self.s.transaction
        @contextlib.contextmanager
        def interleave():
            self.assertEqual(other.claim()["id"], task["id"])
            with original():
                yield
        try:
            with mock.patch.object(self.s, "transaction", interleave):
                with self.assertRaisesRegex(ValueError, "ERR_CANCEL_NOT_QUEUED"):
                    self.c.dispatch("cancel", {"run_id": run["id"], "task_id": task["id"]})
            self.assertEqual(self.s.task(task["id"])["status"], "running")
            self.assertIsNone(self.s.db.execute("SELECT 1 FROM events WHERE task_id=? AND kind='cancelled'", (task["id"],)).fetchone())
        finally:
            other.db.close()

    def test_reschedule_remaining_items_after_a_success_keeps_its_slot(self):
        run = self.begin()
        items = self.payloads(run)
        self.review(run, items)
        first_id = self.complete(run, items[:1])[0]
        first_at = ops.parse_timestamp(items[0]["payload"]["schedule_at"])
        replacement = self.c.dispatch("schedule", {"run_id": run["id"], "replace_unsubmitted": True, "count": 2,
            "first_at": ops.dt.datetime.fromtimestamp(first_at+4200, ops.dt.timezone.utc).isoformat()})
        self.assertEqual(len(replacement["schedule_slots"]), 3)
        self.assertEqual(replacement["schedule_slots"][0], items[0]["payload"]["schedule_at"])
        row = self.s.db.execute("SELECT task_id FROM ops_calendar WHERE at=?", (first_at,)).fetchone()
        self.assertEqual(row[0], first_id)
        self.assertNotIn("schedule_slots", json.loads(self.s.db.execute("SELECT body FROM ops_runs WHERE id=?", (run["id"],)).fetchone()[0]))

    def test_failed_item_closes_as_partial_batch_and_does_not_hold_unused_slots(self):
        self.activate()
        run = self.begin()
        items = self.payloads(run)
        items[0]["payload"]["title"] = "later batch"
        items[1]["payload"]["feed_id"] = "later target"
        self.review(run, items)
        ids = self.complete(run, items)
        self.s.db.execute("UPDATE tasks SET status='failed' WHERE id=?", (ids[1],))
        result = self.c.dispatch("finish", {"run_id": run["id"]})
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["outcomes"]["succeeded"], 1)
        self.assertEqual(result["outcomes"]["failed"], 1)
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM ops_calendar WHERE run_id=?", (run["id"],)).fetchone()[0], 1)
        self.assertFalse(self.c.directory(run["id"]).exists())
        self.assertNotEqual(self.begin()["id"], run["id"])

    def test_one_schedule_slot_cannot_be_used_by_two_different_posts(self):
        run = self.begin(posts=2, comments=1)
        items = self.payloads(run)
        second = json.loads(json.dumps(items[0]))
        second["payload"]["title"] = "different post same slot"
        self.review(run, items + [second])
        self.complete(run, items[:1])
        with self.assertRaisesRegex(ValueError, "ERR_SCHEDULE_ALREADY_USED"):
            self.s.enqueue(second["tool"], second["payload"], "fixture permission", run_id=run["id"])

    def test_repair_claim_without_actual_artifact_report_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "ERR_REPAIR_VERIFICATION_REQUIRED"):
            self.c.dispatch("repair", {key: "claimed only" for key in (
                "failure", "change", "verification", "before_version", "after_version", "rollback_ref", "skill_update")})

    def test_new_batch_cannot_overlap_previously_reserved_publications(self):
        self.activate()
        run = self.begin()
        first = self.s.db.execute("SELECT min(at) FROM ops_calendar").fetchone()[0]
        with self.assertRaisesRegex(ValueError, "ERR_SCHEDULE_CONFLICT"):
            self.c.dispatch("schedule", {"run_id": run["id"], "count": 1, "first_at": ops.dt.datetime.fromtimestamp(first+30*60, ops.dt.timezone.utc).isoformat()})

    def test_new_batch_search_does_not_reuse_previous_batch_snapshot(self):
        run = self.begin()
        old = self.s.enqueue("search_feeds", {"keyword": "fixture"}, run_id=run["id"])
        self.s.db.execute("UPDATE tasks SET status='succeeded' WHERE id=?", (old["id"],))
        self.s.event(old["id"], "succeeded")
        same = self.s.enqueue("search_feeds", {"keyword": "fixture"}, run_id=run["id"])
        self.assertEqual(same["id"], old["id"])
        self.c.dispatch("finish", {"run_id": run["id"], "abandon": True})
        new_run = self.begin()
        new = self.s.enqueue("search_feeds", {"keyword": "fixture"}, run_id=new_run["id"])
        self.assertNotEqual(new["id"], old["id"])


if __name__ == "__main__":
    unittest.main()
