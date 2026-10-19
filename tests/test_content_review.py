"""Content evidence regressions. All accounts, sources and prose are fixtures."""
import json
from pathlib import Path
import unittest

import content_fixture
import test_continuous_ops as lifecycle


class ContentReviewTests(unittest.TestCase):
    setUp = lifecycle.ContinuousTests.setUp
    tearDown = lifecycle.ContinuousTests.tearDown
    begin = lifecycle.ContinuousTests.begin
    payloads = lifecycle.ContinuousTests.payloads
    review = lifecycle.ContinuousTests.review

    def comment(self, target="one"):
        return {"tool": "post_comment_to_feed", "payload": {"feed_id": target, "content": "SYNTHETIC comment " + target}}

    def registered(self, count=1, category="A"):
        run = self.begin(posts=0, comments=count)
        items = [self.comment(str(i)) for i in range(count)]
        content_fixture.register(self.c, run, items, category)
        return run, items

    def review_registered(self, run, items):
        return self.c.dispatch("review", {"run_id": run["id"], "payloads": items, "evidence": "SYNTHETIC cross-item contract record"})

    def stage(self, run, item, name):
        row = self.c.content.latest(run["id"], item["content_id"], name)
        return row, Path(row["body"]["files"][0]["path"])

    def video(self):
        self.c.dispatch("configure", {"allowed_tools": ["publish_with_video", "publish_content", "post_comment_to_feed"]})
        run = self.begin(posts=1, comments=0)
        payload = self.payloads(run)[0]["payload"]
        asset = self.c.directory(run["id"]) / "final/video.mp4"
        asset.write_bytes(b"SYNTHETIC video asset; no real video or platform access")
        del payload["images"]
        payload["video"] = str(asset)
        item = {"tool": "publish_with_video", "payload": payload}
        content_fixture.register(self.c, run, [item])
        return run, item, asset

    def test_boolean_only_review_no_longer_authorizes_copy(self):
        run = self.begin()
        with self.assertRaisesRegex(ValueError, "ERR_CONTENT_STAGE_REQUIRED"):
            self.c.dispatch("review", {"run_id": run["id"], "payloads": [self.comment()], "evidence": "claimed",
                "checks": {k: True for k in ("sources_verified", "facts_checked", "humanizer_checked", "useful_and_original", "targets_checked")}})

    def test_pending_profile_is_saved_but_never_treated_as_confirmation(self):
        p = content_fixture.profile(self.base)
        del p["confirmation_sources"]["allow_c"]
        status = self.c.dispatch("configure", {"content_profile": p})
        self.assertIn("allow_c", status["content_profile_pending"])
        run, items = self.registered()
        with self.assertRaisesRegex(ValueError, "ERR_CONTENT_PROFILE_CONFIRMATION_REQUIRED"):
            self.review_registered(run, items)
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM ops_profile_versions").fetchone()[0], 2)

    def test_confirmed_empty_boundaries_and_latest_c30_fallback_are_supported(self):
        p = content_fixture.profile(self.base)
        p["boundaries"] = []
        p["allow_c"] = True
        p["strategy"] = {"A": 40, "B": 30, "C_max": 30, "fallback": {"A": 40, "B": 60, "C": 0}}
        status = self.c.dispatch("configure", {"content_profile": p})
        self.assertEqual(status["content_profile_pending"], [])
        run = self.begin(posts=0, comments=5, comment_strategy={"A": 2, "B": 3, "C": 0})
        items = [self.comment(str(i)) for i in range(5)]
        content_fixture.register(self.c, run, items[:2], "A")
        content_fixture.register(self.c, run, items[2:], "B")
        self.assertEqual(self.review_registered(run, items)["reviewed_payloads"], 5)

    def test_profile_revision_preserves_active_unknown_task_and_invalidates_review(self):
        run, items = self.registered()
        self.review_registered(run, items)
        task = self.s.enqueue(items[0]["tool"], items[0]["payload"], "synthetic authorization", run_id=run["id"])
        self.s.db.execute("UPDATE tasks SET status='unknown' WHERE id=?", (task["id"],))
        old_policy = self.c.policy()
        p = content_fixture.profile(self.base)
        p["persona"] += " revised"
        self.c.dispatch("configure", {"content_profile": p})
        self.assertEqual(self.c.status()["active_run"]["id"], run["id"])
        self.assertEqual(self.s.task(task["id"])["status"], "unknown")
        self.assertEqual(self.c.policy()["authorization_ref"], old_policy["authorization_ref"])
        self.assertIsNone(self.c.run(run["id"])["body"]["review"])

    def test_portrait_opt_out_needs_no_photo_and_legacy_profile_is_unchanged(self):
        p = content_fixture.profile(self.base)
        self.assertEqual(self.c.content.profile_missing(p), [])
        p["cover_person_enabled"] = False
        p["confirmation_sources"]["cover_person_enabled"] = "fixture opt out"
        self.c.dispatch("configure", {"content_profile": p})
        self.assertEqual(self.c.status()["content_profile_pending"], [])

    def test_portrait_opt_in_requires_real_file_hash_and_user_source(self):
        p = content_fixture.profile(self.base)
        p["cover_person_enabled"] = True
        self.assertIn("cover_person_enabled", self.c.content.profile_missing(p))
        p["confirmation_sources"]["cover_person_enabled"] = "fixture opt in"
        self.c.dispatch("configure", {"content_profile": p})
        self.assertIn("face_reference", self.c.status()["content_profile_pending"])
        photo = self.base / "synthetic-portrait.bin"
        photo.write_bytes(b"fixture bytes only, not a real face")
        import hashlib
        p["face_reference"] = {"file": str(photo), "sha256": hashlib.sha256(photo.read_bytes()).hexdigest(),
                               "source_ref": "synthetic fixture"}
        self.assertIn("face_reference", self.c.content.profile_missing(p))
        p["confirmation_sources"]["face_reference"] = "synthetic fixture"
        self.c.dispatch("configure", {"content_profile": p})
        self.assertEqual(self.c.status()["content_profile_pending"], [])
        photo.write_bytes(b"different file")
        self.assertIn("face_reference", self.c.status()["content_profile_pending"])
        photo.unlink()
        self.assertIn("face_reference", self.c.status()["content_profile_pending"])

    def test_voice_feedback_cannot_be_skipped_before_review(self):
        run, items = self.registered()
        p = content_fixture.profile(self.base)
        p["voice_samples"] = []
        p["confirmation_sources"].pop("voice_samples")
        self.c.dispatch("configure", {"content_profile": p})
        with self.assertRaisesRegex(ValueError, "ERR_CONTENT_PROFILE_CONFIRMATION_REQUIRED.*voice_samples"):
            self.review_registered(run, items)
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)

    def test_stage_requires_source_before_draft_and_distinct_files(self):
        run = self.begin()
        artifact = self.c.directory(run["id"]) / "work/draft.json"
        artifact.write_text(json.dumps({"fields": {"content": "fixture"}}))
        with self.assertRaisesRegex(ValueError, "ERR_CONTENT_STAGE_REQUIRED"):
            self.c.dispatch("update", {"run_id": run["id"], "content_record": {"id": "new", "stage": "draft", "artifact": str(artifact)}})

    def test_source_requires_actual_snapshot_and_all_comment_components(self):
        run, items = self.registered()
        row, path = self.stage(run, items[0], "source")
        value = row["body"]["value"]
        value["sources"][0]["coverage"] = ["body"]
        path = path.with_name("source-missing-comments.json")
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "ERR_CONTENT_SOURCE_REQUIRED"):
            self.c.dispatch("update", {"run_id": run["id"], "content_record": {"id": items[0]["content_id"], "stage": "source", "artifact": str(path)}})

    def test_unchanged_humanizer_edit_is_allowed_with_specific_record(self):
        run, items = self.registered()
        result = self.review_registered(run, items)
        self.assertEqual(result["reviewed_payloads"], 1)
        draft, _ = self.stage(run, items[0], "draft")
        edited, _ = self.stage(run, items[0], "edited")
        self.assertEqual(draft["body"]["value"]["fields"], edited["body"]["value"]["fields"])
        self.assertLess(draft["seq"], edited["seq"])

    def test_humanizer_requires_actual_edit_notes_instead_of_boolean(self):
        run, items = self.registered()
        row, path = self.stage(run, items[0], "edited")
        value = row["body"]["value"]
        value["edit_notes"] = []
        path = path.with_name("edited-no-notes.json")
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "ERR_CONTENT_EDIT_RECORD_REQUIRED"):
            self.c.dispatch("update", {"run_id": run["id"], "content_record": {"id": items[0]["content_id"], "stage": "edited", "artifact": str(path)}})

    def test_final_cannot_add_copy_after_editing(self):
        run, items = self.registered()
        row, path = self.stage(run, items[0], "final")
        value = row["body"]["value"]
        value["fields"]["content"] += " invented later"
        path = path.with_name("final-changed.json")
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "ERR_CONTENT_FINAL_CHANGED"):
            self.c.dispatch("update", {"run_id": run["id"], "content_record": {"id": items[0]["content_id"], "stage": "final", "artifact": str(path)}})

    def test_source_or_stage_change_invalidates_existing_review(self):
        for name in ("source", "draft", "edited", "final"):
            with self.subTest(stage=name):
                run, items = self.registered()
                self.review_registered(run, items)
                _, path = self.stage(run, items[0], name)
                path.write_text(path.read_text() + " ")
                with self.assertRaisesRegex(ValueError, "ERR_CONTENT_REVIEW_STALE"):
                    self.c.authorize(run["id"], items[0]["tool"], items[0]["payload"], "synthetic permission")
                self.c.dispatch("finish", {"run_id": run["id"], "abandon": True})

    def test_source_snapshot_change_invalidates_review(self):
        run, items = self.registered()
        self.review_registered(run, items)
        source, _ = self.stage(run, items[0], "source")
        snapshot = Path(source["body"]["value"]["sources"][0]["snapshot"])
        snapshot.write_text("changed source")
        with self.assertRaisesRegex(ValueError, "ERR_CONTENT_REVIEW_STALE"):
            self.c.authorize(run["id"], items[0]["tool"], items[0]["payload"], "synthetic permission")

    def test_selling_point_evidence_change_invalidates_review(self):
        run, items = self.registered()
        self.review_registered(run, items)
        (self.base / "synthetic-capability.txt").write_text("changed capability")
        with self.assertRaisesRegex(ValueError, "ERR_CONTENT_REVIEW_STALE"):
            self.c.authorize(run["id"], items[0]["tool"], items[0]["payload"], "synthetic permission")

    def test_plan_revision_invalidates_existing_review(self):
        run, items = self.registered()
        self.review_registered(run, items)
        self.c.dispatch("update", {"run_id": run["id"], "plan_ref": "new agreement", "plan": run["body"]["plan"]})
        with self.assertRaisesRegex(ValueError, "ERR_CONTENT_REVIEW_STALE"):
            self.c.authorize(run["id"], items[0]["tool"], items[0]["payload"], "synthetic permission")

    def test_changed_target_or_copy_is_rejected(self):
        run, items = self.registered()
        self.review_registered(run, items)
        changed = dict(items[0]["payload"], content="changed final")
        with self.assertRaisesRegex(ValueError, "ERR_QUALITY_REVIEW_REQUIRED"):
            self.c.authorize(run["id"], items[0]["tool"], changed, "synthetic permission")

    def test_queued_old_review_is_blocked_before_claim_without_attempt(self):
        run, items = self.registered()
        self.review_registered(run, items)
        task = self.s.enqueue(items[0]["tool"], items[0]["payload"], "synthetic permission", run_id=run["id"])
        p = content_fixture.profile(self.base)
        p["objective"] += " revised"
        self.c.dispatch("configure", {"content_profile": p})
        self.assertIsNone(self.s.claim())
        task = self.s.task(task["id"])
        self.assertEqual(task["status"], "blocked")
        self.assertEqual(task["attempts"], 0)
        self.assertEqual(task["error_code"], "ERR_CONTENT_REVIEW_STALE")

    def test_all_a_strategy_cannot_silently_replace_confirmed_a_b_method(self):
        run, items = self.registered(count=2)
        self.c.dispatch("update", {"run_id": run["id"], "plan_ref": "fixture", "plan": {
            "posts": 0, "comments": 2, "comment_strategy": {"A": 2, "B": 0, "C": 0}}})
        with self.assertRaisesRegex(ValueError, "ERR_COMMENT_STRATEGY_CONFIRMATION_REQUIRED"):
            self.review_registered(run, items)

    def test_c_requires_permission_real_demand_and_cap(self):
        run = self.begin(posts=0, comments=5)
        item = self.comment()
        content_fixture.register(self.c, run, [item], "C")
        with self.assertRaisesRegex(ValueError, "ERR_C_DEMAND_PERMISSION_REQUIRED"):
            self.review_registered(run, [item])
        p = content_fixture.profile(self.base)
        p["allow_c"] = True
        self.c.dispatch("configure", {"content_profile": p})
        self.c.dispatch("update", {"run_id": run["id"], "plan_ref": "fixture C allowed", "plan": {
            "posts": 0, "comments": 5, "comment_strategy": {"A": 2, "B": 2, "C": 1}}})
        content_fixture.register(self.c, run, [item], "C")
        self.assertEqual(self.review_registered(run, [item])["reviewed_payloads"], 1)

    def test_b_requires_verified_selling_point(self):
        run, items = self.registered(count=2, category="B")
        # Two B records exceed the agreed one-B slot even though each has a point.
        with self.assertRaisesRegex(ValueError, "ERR_COMMENT_STRATEGY_SCOPE"):
            self.review_registered(run, items)
        row, path = self.stage(run, items[0], "final")
        value = row["body"]["value"]
        value["selling_point_ids"] = ["not-verified"]
        alternate = path.with_name("final-unverified.json")
        alternate.write_text(json.dumps(value))
        self.c.dispatch("update", {"run_id": run["id"], "content_record": {"id": items[0]["content_id"], "stage": "final", "artifact": str(alternate)}})
        with self.assertRaisesRegex(ValueError, "ERR_SELLING_POINT_EVIDENCE_REQUIRED"):
            self.review_registered(run, items[:1])

    def test_post_requires_page_exact_text_and_every_field_review(self):
        run = self.begin(posts=1, comments=1)
        items = self.payloads(run)[:1]
        content_fixture.register(self.c, run, items)
        row, path = self.stage(run, items[0], "final")
        value = row["body"]["value"]
        value["image_reviews"][0]["visible_text"] = "incorrect page transcription"
        alternate = path.with_name("final-wrong-page.json")
        alternate.write_text(json.dumps(value))
        self.c.dispatch("update", {"run_id": run["id"], "content_record": {"id": items[0]["content_id"], "stage": "final", "artifact": str(alternate)}})
        with self.assertRaisesRegex(ValueError, "ERR_PAGE_TEXT_REQUIRED"):
            self.review_registered(run, items)

    def test_video_keeps_independent_text_contract_and_asset_binding(self):
        run, item, asset = self.video()
        self.assertEqual(self.review_registered(run, [item])["reviewed_payloads"], 1)
        row, _ = self.stage(run, item, "final")
        self.assertNotIn("pages", row["body"]["value"]["fields"])
        approved, _ = self.c.authorize(run["id"], item["tool"], item["payload"], "synthetic permission")
        self.assertEqual(approved["content"], item["payload"]["content"])
        self.assertEqual(Path(approved["video"]).read_bytes(), asset.read_bytes())
        asset.write_bytes(b"changed video")
        with self.assertRaisesRegex(ValueError, "ERR_QUALITY_REVIEW_REQUIRED|ERR_CONTENT_REVIEW_STALE"):
            self.c.authorize(run["id"], item["tool"], item["payload"], "synthetic permission")

    def test_video_requires_matching_text_review_and_explained_subtitle_absence(self):
        run, item, _ = self.video()
        row, path = self.stage(run, item, "final")
        value = row["body"]["value"]
        value["video_review"]["video_texts"] = []
        alternate = path.with_name("final-wrong-video-text.json")
        alternate.write_text(json.dumps(value))
        self.c.dispatch("update", {"run_id": run["id"], "content_record": {
            "id": item["content_id"], "stage": "final", "artifact": str(alternate)}})
        with self.assertRaisesRegex(ValueError, "ERR_VIDEO_REVIEW_REQUIRED"):
            self.review_registered(run, [item])
        draft, path = self.stage(run, item, "draft")
        value = draft["body"]["value"]
        value["fields"]["video_texts"] = []
        alternate = path.with_name("draft-no-subtitles.json")
        alternate.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "ERR_VIDEO_TEXT_REQUIRED"):
            self.c.dispatch("update", {"run_id": run["id"], "content_record": {
                "id": item["content_id"], "stage": "draft", "artifact": str(alternate)}})
        value["fields"]["subtitle_absence_reason"] = "SYNTHETIC silent video with no written language"
        alternate.write_text(json.dumps(value))
        self.c.dispatch("update", {"run_id": run["id"], "content_record": {
            "id": item["content_id"], "stage": "draft", "artifact": str(alternate)}})

    def test_incremental_review_keeps_unaffected_items_and_enforces_combined_counts(self):
        run = self.begin(posts=0, comments=2)
        first, second = self.comment("first"), self.comment("second")
        content_fixture.register(self.c, run, [first], "A")
        self.review_registered(run, [first])
        content_fixture.register(self.c, run, [second], "B")
        result = self.review_registered(run, [second])
        self.assertEqual(result["reviewed_payloads"], 2)
        self.c.authorize(run["id"], first["tool"], first["payload"], "synthetic permission")

    def test_preexisting_success_and_unknown_count_against_original_quantity(self):
        run, items = self.registered()
        for i, status in enumerate(("succeeded", "unknown")):
            self.s.db.execute("INSERT INTO tasks(id,account,tool,fingerprint,payload,status,run_id) VALUES (?,?,?,?,?,?,?)",
                ("legacy"+str(i), "fixture", "post_comment_to_feed", "legacy-fingerprint"+str(i), "{}", status, run["id"]))
        with self.assertRaisesRegex(ValueError, "ERR_CONTENT_PLAN_SCOPE"):
            self.review_registered(run, items)


if __name__ == "__main__":
    unittest.main()
