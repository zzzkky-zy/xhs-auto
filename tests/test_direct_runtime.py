"""Direct MCP lifecycle tests use isolated synthetic artifacts, never a site."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

import content_fixture
import test_runtime as fixtures

ops = fixtures.ops


class DirectRuntimeTests(unittest.TestCase):
    setUp = fixtures.RuntimeTests.setUp
    tearDown = fixtures.RuntimeTests.tearDown

    def reviewed(self, tool="post_comment_to_feed", asset_count=1, **changes):
        self.config.data["expected_user_id"] = "fixture-user"
        c = self.store.continuous
        if not c.policy():
            c.dispatch("configure", {"authorization_ref": "SYNTHETIC TEST ONLY", "quality_standard": "offline contract",
                                    "allowed_tools": sorted(ops.WRITES), "content_profile": content_fixture.profile(self.root)})
        post = tool in {"publish_content", "publish_with_video"}
        run = c.dispatch("begin", {"plan_ref": "SYNTHETIC TEST ONLY", "plan": {
            "posts": int(post), "comments": int(not post), "comment_strategy": {"A": int(not post), "B": 0, "C": 0}}})
        if post:
            asset = c.directory(run["id"]) / "final" / ("fixture.png" if tool == "publish_content" else "fixture.mp4")
            asset.write_bytes(b"SYNTHETIC reviewed asset only")
            assets = [str(asset)]
            for index in range(1, asset_count):
                extra = asset.with_name("fixture-" + str(index) + ".png")
                extra.write_bytes(b"SYNTHETIC reviewed extra asset only")
                assets.append(str(extra))
            previous = self.store.db.execute("SELECT max(at) FROM ops_calendar").fetchone()[0] or 0
            slots = c.dispatch("schedule", {"run_id": run["id"], "count": 1,
                                "first_at": ops.dt.datetime.fromtimestamp(max(self.now[0]+7200, previous+3600), ops.dt.timezone.utc).isoformat()})
            payload = {"title": "SYNTHETIC", "content": "offline fixture copy", "schedule_at": slots["schedule_slots"][0],
                       "images" if tool == "publish_content" else "video": assets if tool == "publish_content" else str(asset)}
        else:
            payload = {"feed_id": "fixture", "content": "原帖中的具体建议", "xsec_token": "SYNTHETIC"}
            if tool == "reply_comment_in_feed":
                payload["user_id"] = "fixture-user-target"
        payload.update(changes)
        item = {"tool": tool, "payload": payload}
        content_fixture.register(c, run, [item])
        c.dispatch("review", {"run_id": run["id"], "payloads": [item], "evidence": "SYNTHETIC TEST ONLY"})
        return run, item

    def prepare(self, item, **context):
        return ops.prepare_direct(self.store, {"tool_name": item["tool"], "payload": item["payload"],
                                  "approval_ref": "SYNTHETIC independent permission", **context})

    def receipt(self, prepared, status="succeeded"):
        task = self.store.task(prepared["task_id"])
        p = prepared["payload"]
        if task["tool"] in {"post_comment_to_feed", "reply_comment_in_feed"}:
            result = {"feed_id": p["feed_id"], "success": True}
            if task["tool"] == "reply_comment_in_feed":
                result.update({k: p[field] for k, field in (("target_comment_id", "comment_id"), ("target_user_id", "user_id")) if p.get(field)})
        else:
            result = {"title": p["title"], "content": p["content"], "status": "发布完成"}
            result["images" if task["tool"] == "publish_content" else "video"] = len(p["images"]) if task["tool"] == "publish_content" else p["video"]
        return {"id": task["id"], "tool": task["tool"], "fingerprint": ops.go_mutation_fingerprint(task["tool"], p),
                "updated_at": "2026-10-16T12:34:56.123456789+08:00", "status": status, "result": result}

    def test_four_content_tools_prepare_seal_and_complete_without_transport_or_queue(self):
        for tool in sorted(ops.WRITES):
            with self.subTest(tool=tool):
                self.store.db.execute("DELETE FROM tasks")
                self.store.db.execute("DELETE FROM ops_runs") if self.store.has_table("ops_runs") else None
                run, item = self.reviewed(tool)
                with mock.patch.object(ops, "MCPTransport", side_effect=AssertionError("direct bridge must not recurse into MCP")):
                    prepared = self.prepare(item)
                    task = self.store.task(prepared["task_id"])
                    self.assertEqual((task["status"], task["attempts"], task["auto_run"], task["run_id"]), ("running", 1, 0, run["id"]))
                    self.assertFalse(prepared["managed_by_queue"])
                    self.assertEqual(prepared["operation_id"], task["id"])
                    if tool in {"publish_content", "publish_with_video"}:
                        value = prepared["payload"]["images"][0] if tool == "publish_content" else prepared["payload"]["video"]
                        self.assertIn("/sealed/", value)
                        self.assertEqual(Path(value).read_bytes(), b"SYNTHETIC reviewed asset only")
                        self.assertEqual(prepared["payload"]["schedule_at"], item["payload"]["schedule_at"])
                    receipt = self.receipt(prepared)
                    completed = ops.complete_direct(self.store, {"task_id": task["id"], "operation_status": receipt})
                    self.assertEqual(completed["status"], "succeeded")
                    self.assertTrue(ops.complete_direct(self.store, {"task_id": task["id"], "operation_status": receipt})["duplicate"])
                    self.assertEqual(json.loads(self.store.task(task["id"])["result"]), receipt)
                    self.assertFalse(self.store.status()["runner_enabled"])

    def test_verified_resolution_without_fabricated_result_closes_unknown_once(self):
        _, item = self.reviewed("reply_comment_in_feed", comment_id="target-comment")
        prepared = self.prepare(item)
        unknown = self.receipt(prepared, "unknown")
        unknown.pop("result")
        ops.complete_direct(self.store, {"task_id": prepared["task_id"], "operation_status": unknown})
        confirmed = {**unknown, "status": "confirmed", "evidence": "SYNTHETIC actual platform comment ID and exact content"}
        before = self.store.task(prepared["task_id"])
        for changes in ({"evidence": ""}, {"fingerprint": "wrong"}, {"id": "other"}, {"updated_at": "bad"}):
            with self.assertRaisesRegex(ValueError, "ERR_RECEIPT_MISMATCH"):
                ops.complete_direct(self.store, {"task_id": prepared["task_id"], "operation_status": {**confirmed, **changes}})
            self.assertEqual(self.store.task(prepared["task_id"]), before)
        transport = fixtures.ReceiptTransport(confirmed)
        self.assertEqual(ops.reconcile(self.store, transport, prepared["task_id"])["status"], "confirmed")
        self.assertTrue(ops.reconcile(self.store, transport, prepared["task_id"])["duplicate"])
        self.assertEqual(self.store.task(prepared["task_id"])["attempts"], before["attempts"])
        self.assertNotIn("result", json.loads(self.store.task(prepared["task_id"])["result"]))
        self.assertFalse(self.store.status()["runner_enabled"])

    def test_actual_private_cli_bridge_completes_one_call_without_http_recursion(self):
        run, item = self.reviewed()
        self.config_path.write_text(json.dumps(self.config.data))

        def bridge(request):
            process = subprocess.run([sys.executable, str(Path(ops.__file__).resolve()), "--config", str(self.config_path), "bridge"],
                                     input=json.dumps(request), text=True, capture_output=True, timeout=5)
            self.assertEqual(process.returncode, 0, process.stderr)
            return json.loads(process.stdout)

        prepared = bridge({"action": "prepare_direct", "tool_name": item["tool"], "payload": item["payload"],
                           "run_id": run["id"], "approval_ref": "SYNTHETIC explicit permission", "operation_id": "cli-original-id"})
        completed = bridge({"action": "complete_direct", "task_id": prepared["task_id"], "operation_status": self.receipt(prepared)})
        self.assertEqual((prepared["task_id"], completed["status"]), ("cli-original-id", "succeeded"))
        self.assertFalse(self.store.status()["runner_enabled"])

    def test_manage_metadata_does_not_change_original_fingerprint(self):
        _, item = self.reviewed()
        original = self.store.fingerprint(item["tool"], item["payload"])
        changed = {**item["payload"], "operation_id": "old", "run_id": "run", "approval_ref": "another", "xsec_token": "rotated"}
        self.assertEqual(original, self.store.fingerprint(item["tool"], changed))
        self.assertEqual(ops.go_mutation_fingerprint(item["tool"], changed), "afc7e0ad948732e6dc791a2a548d878299ce2afc7dcfcac1e0e2e43d18a1b300")

    def test_new_caller_operation_id_is_preserved_without_a_separate_submit_call(self):
        _, item = self.reviewed()
        prepared = self.prepare(item, operation_id="caller-original-operation-123")
        self.assertEqual(prepared["task_id"], "caller-original-operation-123")
        self.assertEqual(prepared["payload"]["operation_id"], prepared["task_id"])
        self.assertEqual(self.prepare(item)["task_id"], prepared["task_id"])
        different = {**item, "payload": {**item["payload"], "content": "different content"}}
        with self.assertRaisesRegex(ValueError, "ERR_RECEIPT_MISMATCH"):
            self.prepare(different, operation_id=prepared["task_id"])

    def test_original_go_fingerprint_vectors_for_four_typed_requests(self):
        vectors = [
            ("post_comment_to_feed", {"feed_id": "feed-1", "content": "准确稿 <&>\u2028"}, "17f60c29a79d725a9db0b6d0ccdee4f76fa47f1611a2c786f640526facb0eac6"),
            ("reply_comment_in_feed", {"feed_id": "feed-1", "comment_id": "comment-2", "user_id": "user-3", "content": "回复&文本"}, "73b1fb8bda2ef666e6b9b2f2176c457e60c23450eb0fbb7c3f543c1f0ac789ec"),
            ("publish_content", {"title": "标题", "content": "正文<&>", "images": ["https://fixture.invalid/one.png?x=1&y=2"], "tags": ["求职", "面试"], "schedule_at": "2030-10-20T10:00:00+08:00", "is_original": True, "visibility": "仅自己可见", "products": ["fixture-product"], "activity": "fixture-activity", "long_article_template": "fixture-long", "topic_template": "fixture-topic"}, "b84fa30e8dc8a3afbdc4733932686ae26c9f8c9fd31e35f60f0541bfcd405f96"),
            ("publish_with_video", {"title": "视频标题", "content": "正文", "video": "https://fixture.invalid/clip.mp4", "tags": ["面试"], "schedule_at": "2030-10-20T12:00:00+08:00", "is_original": True, "visibility": "公开可见", "products": ["fixture-product"], "activity": "fixture-activity"}, "b50bf01a3fcdab278277ad1155b7bf7cc2a63c2e440d86792864d0734f4a33ad"),
        ]
        for tool, payload, expected in vectors:
            with self.subTest(tool=tool):
                self.assertEqual(ops.go_mutation_fingerprint(tool, payload), expected)

    def test_missing_review_stale_profile_and_changed_copy_are_rejected_before_task(self):
        with self.assertRaisesRegex(ValueError, "ERR_QUALITY_REVIEW_REQUIRED"):
            self.prepare({"tool": "post_comment_to_feed", "payload": {"feed_id": "fixture", "content": "not reviewed"}})
        run, item = self.reviewed()
        changed = {**item, "payload": {**item["payload"], "content": "changed"}}
        with self.assertRaisesRegex(ValueError, "ERR_QUALITY_REVIEW_REQUIRED"):
            self.prepare(changed, run_id=run["id"])
        profile = self.store.continuous.content.profile()[1]
        profile["persona"] += " changed"
        self.store.continuous.dispatch("configure", {"content_profile": profile})
        with self.assertRaisesRegex(ValueError, "ERR_CONTENT_REVIEW_STALE"):
            self.prepare(item, run_id=run["id"])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM tasks").fetchone()[0], 0)

    def test_ambiguous_review_needs_run_id_and_never_guesses(self):
        run, item = self.reviewed()
        twin = "f" * 32
        self.store.db.execute("INSERT INTO ops_runs SELECT ?,state,created,updated,body FROM ops_runs WHERE id=?", (twin, run["id"]))
        # The same live review existing twice is ambiguous even when both
        # entries otherwise bind exactly the same immutable evidence.
        with mock.patch.object(self.store.continuous.content, "authorize"):
            with self.assertRaisesRegex(ValueError, "ERR_CONTENT_MATCH_AMBIGUOUS"):
                self.prepare(item)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM tasks").fetchone()[0], 0)

    def test_existing_queue_task_reuses_original_owner_attempt_and_review(self):
        run, item = self.reviewed()
        queued = self.store.enqueue(item["tool"], item["payload"], "SYNTHETIC queue permission", run_id=run["id"])
        self.store.db.execute("UPDATE tasks SET auto_run=1 WHERE id=?", (queued["id"],))
        task = self.store.claim()
        prepared = self.prepare(item, operation_id=task["id"])
        self.assertTrue(prepared["managed_by_queue"])
        self.assertEqual(self.store.task(task["id"]), task)
        with self.assertRaisesRegex(ValueError, "ERR_RECEIPT_TASK_BINDING"):
            ops.complete_direct(self.store, {"task_id": task["id"], "operation_status": self.receipt(prepared)})

    def test_unknown_older_work_requires_independent_authorization_and_is_preserved(self):
        old = self.store.enqueue("post_comment_to_feed", {"feed_id": "old", "content": "old"}, "SYNTHETIC permission")["id"]
        self.store.finish(self.store.claim(), "unknown", "ERR_NETWORK")
        run, item = self.reviewed()
        with self.assertRaisesRegex(ValueError, "ERR_APPROVAL_REQUIRED"):
            self.prepare(item, approval_ref="")
        prepared = self.prepare(item, run_id=run["id"])
        ops.complete_direct(self.store, {"task_id": prepared["task_id"], "operation_status": self.receipt(prepared)})
        self.assertEqual(self.store.task(old)["status"], "unknown")
        self.assertFalse(self.store.status()["runner_enabled"])

    def test_unknown_receipt_and_restart_keep_same_id_and_cannot_release_without_receipt(self):
        _, item = self.reviewed()
        prepared = self.prepare(item)
        unknown = self.receipt(prepared, "unknown")
        unknown.pop("result")
        ops.complete_direct(self.store, {"task_id": prepared["task_id"], "operation_status": unknown})
        again = self.prepare(item)
        self.assertEqual(again["task_id"], prepared["task_id"])
        with self.assertRaisesRegex(ValueError, "ERR_TASK_UNRESOLVED"):
            ops.complete_direct(self.store, {"task_id": prepared["task_id"], "error_code": "ERR_RATE_LIMIT"})
        self.assertEqual(self.store.task(prepared["task_id"])["status"], "unknown")
        self.assertIsNone(self.store.claim())

    def test_caller_cancel_and_unproved_success_are_unknown(self):
        _, item = self.reviewed()
        prepared = self.prepare(item)
        completed = ops.complete_direct(self.store, {"task_id": prepared["task_id"], "result": {"success": True}})
        self.assertEqual(completed["status"], "unknown")
        self.assertEqual(self.store.task(prepared["task_id"])["attempts"], 1)

    def test_rate_limit_returns_original_deadline_does_not_spend_attempt_or_enable_queue(self):
        _, item = self.reviewed()
        prepared = self.prepare(item)
        completed = ops.complete_direct(self.store, {"task_id": prepared["task_id"], "error_code": "ERR_RATE_LIMIT", "retry_at": 1120})
        task = self.store.task(prepared["task_id"])
        self.assertEqual((completed["status"], task["attempts"], task["next_at"], task["auto_run"]), ("queued", 0, 1120, 0))
        self.assertFalse(self.store.status()["runner_enabled"])
        self.assertIsNone(self.store.claim(automatic=True))

    def test_resumed_not_applied_receipt_does_not_override_new_local_deferral(self):
        _, item = self.reviewed()
        original = self.prepare(item)
        task_id = original["task_id"]
        prior = self.receipt(original, "not_applied")
        self.assertEqual(ops.complete_direct(self.store, {"task_id": task_id, "operation_status": prior})["status"], "blocked")
        self.store.resume(task_id, "SYNTHETIC input repair verified")
        resumed = self.prepare(item, operation_id=task_id)
        deadline = self.now[0] + 20
        completed = ops.complete_direct(self.store, {"task_id": task_id, "operation_status": prior,
                                        "error_code": "ERR_RATE_LIMIT", "retry_at": deadline})
        task = self.store.task(task_id)
        self.assertEqual((completed["status"], task["attempts"], task["next_at"], task["auto_run"]), ("queued", 1, deadline, 0))
        self.assertFalse(completed["receipt_imported"])
        self.assertIsNone(task["owner"])
        self.assertIsNone(self.store.claim(automatic=True))
        self.now[0] = deadline
        resumed = self.prepare(item, operation_id=task_id)
        completed = ops.complete_direct(self.store, {"task_id": task_id, "operation_status": self.receipt(resumed)})
        self.assertEqual((completed["status"], self.store.task(task_id)["attempts"]), ("succeeded", 2))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM tasks").fetchone()[0], 1)

    def test_local_deferral_still_validates_prior_receipt_and_retains_uncertain_send(self):
        _, item = self.reviewed()
        prepared = self.prepare(item)
        task_id = prepared["task_id"]
        original = self.store.task(task_id)
        for change in ({"id": "other"}, {"fingerprint": "changed"}, {"updated_at": "undated"}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "ERR_RECEIPT_MISMATCH"):
                ops.complete_direct(self.store, {"task_id": task_id, "operation_status": {**self.receipt(prepared, "not_applied"), **change},
                                                "error_code": "ERR_RATE_LIMIT", "retry_at": self.now[0]+20})
            self.assertEqual(self.store.task(task_id), original)
        uncertain = self.receipt(prepared, "unknown")
        completed = ops.complete_direct(self.store, {"task_id": task_id, "operation_status": uncertain,
                                        "error_code": "ERR_RATE_LIMIT", "retry_at": self.now[0]+20})
        self.assertEqual(completed["status"], "unknown")
        self.assertTrue(completed["receipt_imported"])
        with self.assertRaisesRegex(ValueError, "ERR_TASK_UNRESOLVED"):
            ops.complete_direct(self.store, {"task_id": task_id, "operation_status": self.receipt(prepared, "not_applied"),
                                            "error_code": "ERR_RATE_LIMIT", "retry_at": self.now[0]+20})
        self.assertEqual(self.store.task(task_id)["status"], "unknown")

    def test_receipt_identity_result_and_date_mismatch_preserve_running_evidence(self):
        _, item = self.reviewed()
        prepared = self.prepare(item)
        original = self.store.task(prepared["task_id"])
        for change in ({"id": "other"}, {"tool": "reply_comment_in_feed"}, {"fingerprint": original["fingerprint"]},
                       {"updated_at": "2026-10-16"}, {"result": {"success": True, "feed_id": "other"}}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "ERR_RECEIPT_MISMATCH"):
                ops.complete_direct(self.store, {"task_id": prepared["task_id"], "operation_status": {**self.receipt(prepared), **change}})
            self.assertEqual(self.store.task(prepared["task_id"]), original)

    def test_expired_direct_lease_recovers_to_unknown_without_a_new_task(self):
        _, item = self.reviewed()
        prepared = self.prepare(item)
        self.now[0] += self.config.lease + 1
        self.assertIsNone(self.store.claim())
        self.assertEqual(self.store.task(prepared["task_id"])["status"], "unknown")
        self.assertEqual(self.prepare(item)["task_id"], prepared["task_id"])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM tasks").fetchone()[0], 1)

    def test_terminal_known_id_checks_original_parameters_then_returns_receipt_only(self):
        _, item = self.reviewed()
        prepared = self.prepare(item)
        task_id = prepared["task_id"]
        for status in ("unknown", "succeeded", "confirmed"):
            with self.subTest(status=status):
                self.store.db.execute("UPDATE tasks SET status=?,owner=NULL,lease_until=NULL WHERE id=?", (status, task_id))
                before = self.store.task(task_id)
                with mock.patch.object(self.store, "fingerprint", side_effect=AssertionError("new binding does not recompute the original fingerprint")):
                    replay = self.prepare(item, operation_id=task_id)
                    for change in ({"content": "changed original body"}, {"feed_id": "different-target"}, {"content": None}):
                        with self.assertRaisesRegex(ValueError, "ERR_RECEIPT_MISMATCH"):
                            self.prepare({"tool": item["tool"], "payload": {**item["payload"], **change}}, operation_id=task_id)
                self.assertTrue(replay["receipt_only"])
                self.assertNotIn("payload", replay)
                self.assertEqual((replay["operation_id"], replay["status"]), (task_id, status))
                self.assertEqual(self.store.task(task_id), before)

    def test_terminal_binding_rejects_body_targets_topics_and_schedule_changes_for_four_tools(self):
        for tool in sorted(ops.WRITES):
            with self.subTest(tool=tool):
                self.store.db.execute("DELETE FROM tasks")
                self.store.db.execute("DELETE FROM ops_runs") if self.store.has_table("ops_runs") else None
                _, item = self.reviewed(tool)
                prepared = self.prepare(item)
                task_id = prepared["task_id"]
                self.store.db.execute("UPDATE tasks SET status='succeeded',owner=NULL,lease_until=NULL WHERE id=?", (task_id,))
                changes = [{"content": "different body"}]
                if tool in {"post_comment_to_feed", "reply_comment_in_feed"}:
                    changes += [{"feed_id": "different-feed"}]
                    if tool == "reply_comment_in_feed":
                        changes += [{"comment_id": "different-comment"}, {"user_id": "different-user"}]
                else:
                    changes += [{"title": "different-title"}, {"tags": ["different-topic"]},
                                {"schedule_at": "2026-10-17T12:00:00+08:00"}, {"is_original": False}]
                    if tool == "publish_content":
                        changes += [{"topic_template": "different-template"}]
                before = self.store.task(task_id)
                for change in changes:
                    with self.subTest(change=change), self.assertRaisesRegex(ValueError, "ERR_RECEIPT_MISMATCH"):
                        self.prepare({"tool": tool, "payload": {**item["payload"], **change}}, operation_id=task_id)
                    self.assertEqual(self.store.task(task_id), before)
                rotated = {"tool": tool, "payload": {**item["payload"], "xsec_token": "ROTATED-SYNTHETIC",
                                                    "operation_id": task_id, "run_id": "management-only", "approval_ref": "management-only"}}
                self.assertTrue(self.prepare(rotated, operation_id=task_id)["receipt_only"])

    def test_terminal_same_path_changed_media_is_conflict_even_if_another_asset_was_cleaned(self):
        for tool in ("publish_content", "publish_with_video"):
            with self.subTest(tool=tool):
                self.store.db.execute("DELETE FROM tasks")
                self.store.db.execute("DELETE FROM ops_runs") if self.store.has_table("ops_runs") else None
                _, item = self.reviewed(tool, asset_count=2 if tool == "publish_content" else 1)
                prepared = self.prepare(item)
                task_id = prepared["task_id"]
                self.store.db.execute("UPDATE tasks SET status='succeeded',owner=NULL,lease_until=NULL WHERE id=?", (task_id,))
                paths = item["payload"]["images"] if tool == "publish_content" else [item["payload"]["video"]]
                if len(paths) > 1:
                    Path(paths[0]).unlink()
                Path(paths[-1]).write_bytes(b"changed bytes under original path")
                before = self.store.task(task_id)
                with self.assertRaisesRegex(ValueError, "ERR_RECEIPT_MISMATCH"):
                    self.prepare(item, operation_id=task_id)
                self.assertEqual(self.store.task(task_id), before)

    def test_legacy_terminal_binding_uses_fingerprint_or_requires_native_receipt_query(self):
        _, item = self.reviewed("publish_content")
        prepared = self.prepare(item)
        task_id = prepared["task_id"]
        self.store.db.execute("UPDATE tasks SET status='succeeded',owner=NULL,lease_until=NULL,request_binding=NULL WHERE id=?", (task_id,))
        self.assertTrue(self.prepare(item, operation_id=task_id)["receipt_only"])
        with self.assertRaisesRegex(ValueError, "ERR_RECEIPT_MISMATCH"):
            self.prepare({"tool": item["tool"], "payload": {**item["payload"], "content": "changed legacy body"}}, operation_id=task_id)
        Path(item["payload"]["images"][0]).unlink()
        before = self.store.task(task_id)
        for payload in (item["payload"], {**item["payload"], "content": "unverifiable legacy body"}):
            with self.assertRaisesRegex(ValueError, "ERR_RECEIPT_QUERY_REQUIRED"):
                self.prepare({"tool": item["tool"], "payload": payload}, operation_id=task_id)
        self.assertEqual(self.store.task(task_id), before)

    def test_core_history_compaction_preserves_durable_request_binding(self):
        item = {"tool": "post_comment_to_feed", "payload": {"feed_id": "fixture", "content": "original body", "xsec_token": "SYNTHETIC"}}
        queued = self.store.enqueue(item["tool"], item["payload"], "SYNTHETIC original permission")
        self.store.finish(self.store.claim(), "succeeded", result={"success": True})
        original = self.store.task(queued["id"])
        self.now[0] += 8*86400
        self.assertEqual(self.store.maintain_history()["details_compacted"], 1)
        restarted = ops.Store(self.config, now=lambda: self.now[0])
        try:
            compacted = restarted.task(queued["id"])
            self.assertEqual((compacted["request_binding"], compacted["fingerprint"]),
                             (original["request_binding"], original["fingerprint"]))
            self.assertNotIn("content", json.loads(compacted["payload"]))
            replay = ops.prepare_direct(restarted, {"tool_name": item["tool"], "payload": item["payload"], "operation_id": queued["id"]})
            self.assertTrue(replay["receipt_only"])
            with self.assertRaisesRegex(ValueError, "ERR_RECEIPT_MISMATCH"):
                ops.prepare_direct(restarted, {"tool_name": item["tool"], "payload": {**item["payload"], "content": "different body"},
                                              "operation_id": queued["id"]})
        finally:
            restarted.db.close()

    def test_compacted_successful_media_replay_and_reconcile_do_not_read_deleted_assets(self):
        for tool in ("publish_content", "publish_with_video"):
            with self.subTest(tool=tool):
                self.store.db.execute("DELETE FROM tasks")
                self.store.db.execute("DELETE FROM ops_runs") if self.store.has_table("ops_runs") else None
                policy = self.store.continuous.policy()
                if policy:
                    policy.update(allowed_tools=sorted(ops.WRITES), mode="validation_required")
                    self.store.continuous.save_policy(policy)
                run, item = self.reviewed(tool)
                prepared = self.prepare(item)
                receipt = self.receipt(prepared)
                ops.complete_direct(self.store, {"task_id": prepared["task_id"], "operation_status": receipt})
                # Finish this synthetic single-workflow validation through its
                # ordinary accepted cleanup boundary, which removes the media.
                policy = self.store.continuous.policy()
                policy["allowed_tools"] = [tool]
                self.store.continuous.save_policy(policy)
                self.store.continuous.dispatch("finish", {"run_id": run["id"]})
                self.store.continuous.dispatch("accept", {"run_id": run["id"], "human_acceptance_ref": "SYNTHETIC cleanup acceptance"})
                self.assertFalse(self.store.continuous.directory(run["id"]).exists())
                before = self.store.task(prepared["task_id"])
                self.assertTrue(before["request_binding"])
                with mock.patch.object(self.store, "fingerprint", side_effect=AssertionError("no deleted media hashing")), \
                     mock.patch.object(ops, "go_mutation_fingerprint", side_effect=AssertionError("no deleted media signature")):
                    replay = self.prepare(item, operation_id=prepared["task_id"])
                    self.assertTrue(replay["receipt_only"])
                    self.assertNotIn("payload", replay)
                    reconciled = ops.reconcile(self.store, fixtures.ReceiptTransport(receipt), prepared["task_id"])
                    self.assertTrue(reconciled["duplicate"])
                self.assertEqual(self.store.task(prepared["task_id"]), before)

    def test_manual_not_before_and_dependency_wait_without_claiming_or_spending_attempts(self):
        run, item = self.reviewed()
        dependency = self.store.enqueue("get_runtime_status", {})["id"]
        queued = self.store.enqueue(item["tool"], item["payload"], "SYNTHETIC manual permission",
                                    not_before=1120, dependency=dependency, run_id=run["id"])
        task_id = queued["id"]
        before = self.store.task(task_id)
        deferred = self.prepare(item, operation_id=task_id)
        self.assertEqual((deferred["deferred"], deferred["error_code"], deferred["dependency_id"]), (True, "ERR_DEPENDENCY_PENDING", dependency))
        self.assertEqual(self.store.task(task_id), before)
        self.store.db.execute("UPDATE tasks SET status='succeeded' WHERE id=?", (dependency,))
        deferred = self.prepare(item, operation_id=task_id)
        self.assertEqual((deferred["error_code"], deferred["retry_at"]), ("ERR_RATE_LIMIT", 1120))
        self.assertEqual(self.store.task(task_id), before)
        self.now[0] = 1120
        ready = self.prepare(item, operation_id=task_id)
        self.assertFalse(ready.get("deferred", False))
        self.assertEqual((self.store.task(task_id)["status"], self.store.task(task_id)["attempts"]), ("running", 1))

    def test_failed_manual_dependency_is_not_bypassed_by_direct_call(self):
        run, item = self.reviewed()
        dependency = self.store.enqueue("get_runtime_status", {})["id"]
        queued = self.store.enqueue(item["tool"], item["payload"], "SYNTHETIC permission", dependency=dependency, run_id=run["id"])
        self.store.db.execute("UPDATE tasks SET status='failed' WHERE id=?", (dependency,))
        before = self.store.task(queued["id"])
        deferred = self.prepare(item, operation_id=queued["id"])
        self.assertEqual(deferred["error_code"], "ERR_DEPENDENCY_FAILED")
        self.assertTrue(deferred["deferred"])
        self.assertEqual(self.store.task(queued["id"]), before)

    def test_disabled_write_uses_write_not_allowed_for_direct_submit_and_claim(self):
        _, item = self.reviewed()
        queued = self.store.enqueue(item["tool"], item["payload"], "SYNTHETIC permission")
        self.config.allow_write = False
        with self.assertRaisesRegex(ValueError, "ERR_WRITE_NOT_ALLOWED"):
            self.prepare(item)
        with self.assertRaisesRegex(ValueError, "ERR_WRITE_NOT_ALLOWED"):
            ops.bridge(self.config, {"action": "submit", "tool": item["tool"], "payload": item["payload"], "approval_ref": "SYNTHETIC permission"})
        self.assertIsNone(self.store.claim())
        self.assertEqual(self.store.task(queued["id"])["error_code"], "ERR_WRITE_NOT_ALLOWED")

    def test_four_tools_can_reconcile_receipt_after_restart_or_completion_failure(self):
        for tool in sorted(ops.WRITES):
            with self.subTest(tool=tool):
                self.store.db.execute("DELETE FROM tasks")
                self.store.db.execute("DELETE FROM ops_runs") if self.store.has_table("ops_runs") else None
                _, item = self.reviewed(tool)
                prepared = self.prepare(item)
                self.now[0] += self.config.lease + 1
                restarted = ops.Store(self.config, now=lambda: self.now[0])
                try:
                    receipt = self.receipt(prepared)
                    transport = fixtures.ReceiptTransport(receipt)
                    self.assertEqual(ops.reconcile(restarted, transport, prepared["task_id"])["status"], "succeeded")
                    self.assertEqual(restarted.task(prepared["task_id"])["attempts"], 1)
                    self.assertTrue(ops.reconcile(restarted, transport, prepared["task_id"])["duplicate"])
                    self.assertEqual(len(transport.calls), 2)
                    self.assertFalse(restarted.status()["runner_enabled"])
                finally:
                    restarted.db.close()

    def test_basic_store_and_state_check_do_not_load_content_modules_or_create_ops_tables(self):
        self.assertIsNone(self.store._continuous)
        self.store.verify_state()
        self.store.enqueue("get_runtime_status", {})
        self.assertFalse(self.store.has_table("ops_runs"))
        self.assertEqual(self.store.status()["continuous_operation"]["mode"], "not_configured")
        self.assertFalse(self.store.has_table("ops_runs"))

    def test_status_without_task_database_creates_no_state_and_loads_no_batch_modules(self):
        config_data = {**self.config_data, "state_dir": "absent-status-state", "output_dir": "absent-status-output"}
        config_path = self.root / "status-only.json"
        config_path.write_text(json.dumps(config_data))
        script = """import json,sys
sys.path.insert(0, sys.argv[1])
import xhs_ops
value = xhs_ops.bridge(xhs_ops.Config(sys.argv[2]), {"action": "status"})
print(json.dumps({"value": value, "batch_modules": sorted(set(sys.modules) & {"continuous_ops", "content_review"})}))
"""
        process = subprocess.run([sys.executable, "-c", script, str(Path(ops.__file__).resolve().parent), str(config_path)],
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["batch_modules"], [])
        self.assertEqual((result["value"]["status"], result["value"]["queue_available"], result["value"]["runner_enabled"]),
                         ("not_initialized", False, False))
        self.assertIsNone(result["value"]["background_next_at"])
        cli = subprocess.run([sys.executable, str(Path(ops.__file__).resolve()), "--config", str(config_path), "status"],
                             capture_output=True, text=True, timeout=10)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertEqual(json.loads(cli.stdout)["status"], "not_initialized")
        self.assertFalse((self.root / "absent-status-state").exists())
        self.assertFalse((self.root / "absent-status-output").exists())

    def test_queue_bootstrap_is_read_only_and_respects_existing_paused_runner(self):
        task_id = self.store.enqueue("get_runtime_status", {})["id"]
        self.store.db.execute("UPDATE tasks SET auto_run=1 WHERE id=?", (task_id,))
        self.assertEqual(ops.queue_bootstrap_flags(self.config), {"XHS_OPS_QUEUE_PENDING": "0", "XHS_OPS_STATE_MAINTENANCE": "1"})
        self.store.db.execute("INSERT OR REPLACE INTO meta VALUES ('runner_enabled','1')")
        self.assertEqual(ops.queue_bootstrap_flags(self.config)["XHS_OPS_QUEUE_PENDING"], "1")
        self.assertFalse(self.store.has_table("ops_runs"))
        other = copy.copy(self.config)
        other.state_dir = self.root / "missing-state"
        self.assertEqual(ops.queue_bootstrap_flags(other), {"XHS_OPS_QUEUE_PENDING": "0", "XHS_OPS_STATE_MAINTENANCE": "0"})
        self.assertFalse(other.state_dir.exists())

    def test_legacy_terminal_task_replays_original_receipt_without_direct_accounting(self):
        _, item = self.reviewed()
        task_id = self.store.enqueue(item["tool"], item["payload"], "SYNTHETIC permission")["id"]
        task = self.store.claim()
        self.store.finish(task, "succeeded", result={"fixture": True})
        prepared = self.prepare(item, operation_id=task_id)
        self.assertEqual(prepared["task_id"], task_id)
        self.assertTrue(prepared["managed_by_queue"])
        self.assertEqual(self.store.task(task_id)["attempts"], 1)

    def test_repair_requires_actual_capabilities_instead_of_exactly_thirty_tools(self):
        binary = self.root / "synthetic-binary"
        binary.write_bytes(b"SYNTHETIC TEST ONLY")
        self.config.data["mcp_binary"] = str(binary)
        required = ops.WRITES | {"get_runtime_status", "get_operation_status", "submit_task", "get_task_status",
                                 "control_task_queue", "manage_operation_run", "reconcile_task", "resume_task"}
        root = Path(ops.__file__).resolve().parents[1]
        sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        report = {"version": (root / "VERSION").read_text().strip(), "status": "passed", "source_dirty": False,
                  "source_commit": "SYNTHETIC TEST ONLY", "actual_local_stdio_tools": len(required),
                  "actual_local_stdio_capabilities": sorted(required), "records": [{"exit_code": 0}],
                  "binary_sha256": sha(binary), "runtime_sha256": sha(root / "runtime/xhs_ops.py"),
                  "stdio_entry_sha256": sha(root / "runtime/codex_mcp.py"),
                  "runtime_files_sha256": {path.name: sha(path) for path in sorted((root / "runtime").glob("*.py"))}}
        path = self.root / "SYNTHETIC-REPORT.json"
        path.write_text(json.dumps(report))
        data = {key: "SYNTHETIC TEST ONLY" for key in ("failure", "change", "verification", "before_version", "rollback_ref", "skill_update")}
        data.update(after_version=report["version"], verification_report=str(path))
        self.assertEqual(self.store.continuous.repair(data)["verified_version"], report["version"])
        report["actual_local_stdio_capabilities"].remove("resume_task")
        report["actual_local_stdio_tools"] -= 1
        path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "ERR_REPAIR_VERIFICATION_REQUIRED"):
            self.store.continuous.repair(data)

    def test_removed_research_page_option_is_ignored_for_config_and_runtime_identity(self):
        before = self.config.fingerprint()
        self.config_data.update(expected_state_id=self.config.state_id, reuse_research_page="removed legacy option")
        self.config_path.write_text(json.dumps(self.config_data))
        self.assertEqual(ops.Config(self.config_path).fingerprint(), before)


if __name__ == "__main__":
    unittest.main()
