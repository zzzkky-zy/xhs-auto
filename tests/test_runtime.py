import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import content_fixture

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("xhs_ops", ROOT / "runtime/xhs_ops.py")
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)


class FakeMCP(BaseHTTPRequestHandler):
    calls = []
    outcome = "success"
    records = {}

    def log_message(self, *_):
        pass

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if req["method"] == "notifications/initialized":
            self.send_response(202); self.end_headers(); return
        result = {}
        if req["method"] == "initialize":
            result = {"protocolVersion": "2025-11-25", "capabilities": {}, "serverInfo": {"name": "test", "version": "1"}}
        elif req["method"] == "tools/call":
            args = req["params"]
            self.calls.append(args)
            name, data = args["name"], args["arguments"]
            if name == "get_operation_status":
                result = {"content": [{"type": "text", "text": json.dumps(self.records.get(data["operation_id"], {"status": "unknown"}))}]}
            elif self.outcome == "network":
                self.connection.shutdown(2); self.connection.close(); return
            elif self.outcome == "success":
                result = {"content": [{"type": "text", "text": json.dumps({"verified": True, "tool": name})}]}
            else:
                result = {"isError": True, "content": [{"type": "text", "text": self.outcome}]}
        raw = json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": result}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)


class ReceiptTransport:
    """Read-only durable receipt fixture; a write here is a test failure."""
    def __init__(self, result=None, error=None, before_return=None):
        self.result, self.error, self.before_return = result, error, before_return
        self.calls = []

    def call(self, name, payload):
        if name != "get_operation_status":
            raise AssertionError("reconcile must never send a platform write")
        self.calls.append((name, payload))
        if self.before_return:
            self.before_return()
        if self.error:
            raise self.error
        return self.result


class ConfigPacingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "config.json"
        self.base = {"schema_version": 1, "account_id": "pacing-fixture", "state_dir": "state", "output_dir": "output",
                     "mcp_url": "http://127.0.0.1:1/mcp", "cdp_url": "http://127.0.0.1:1"}

    def config(self, **settings):
        self.path.write_text(json.dumps({**self.base, **settings}))
        return ops.Config(self.path)

    def test_default_random_read_and_search_ranges_are_effective(self):
        config = self.config()
        env = config.runtime_env()
        self.assertEqual((config.search_min, config.search_max, config.read_min, config.read_max), (60, 90, 10, 20))
        self.assertEqual({name: env[name] for name in ("XHS_SEARCH_INTERVAL_MIN", "XHS_SEARCH_INTERVAL_MAX", "XHS_READ_INTERVAL_MIN", "XHS_READ_INTERVAL_MAX")},
                         {"XHS_SEARCH_INTERVAL_MIN": "60s", "XHS_SEARCH_INTERVAL_MAX": "90s", "XHS_READ_INTERVAL_MIN": "10s", "XHS_READ_INTERVAL_MAX": "20s"})
        self.assertEqual((env["XHS_SEARCH_INTERVAL"], env["XHS_READ_INTERVAL"]), ("", ""))

    def test_explicit_legacy_intervals_stay_fixed(self):
        config = self.config(search_interval_seconds=75, read_interval_seconds=15)
        env = config.runtime_env()
        self.assertEqual((env["XHS_SEARCH_INTERVAL_MIN"], env["XHS_SEARCH_INTERVAL_MAX"]), ("75s", "75s"))
        self.assertEqual((env["XHS_READ_INTERVAL_MIN"], env["XHS_READ_INTERVAL_MAX"]), ("15s", "15s"))
        explicit = self.config(search_interval_min_seconds=75, search_interval_max_seconds=75,
                               read_interval_min_seconds=15, read_interval_max_seconds=15)
        self.assertEqual(config.fingerprint(), explicit.fingerprint())

    def test_explicit_ranges_override_legacy_values_like_write_pacing(self):
        config = self.config(search_interval_seconds=75, search_interval_min_seconds=65, search_interval_max_seconds=85,
                             read_interval_seconds=15, read_interval_min_seconds=12, read_interval_max_seconds=18)
        self.assertEqual((config.search_min, config.search_max, config.read_min, config.read_max), (65, 85, 12, 18))
        partial = self.config(search_interval_seconds=75, search_interval_min_seconds=65,
                              read_interval_seconds=15, read_interval_max_seconds=18)
        self.assertEqual((partial.search_min, partial.search_max, partial.read_min, partial.read_max), (65, 75, 15, 18))

    def test_effective_ranges_participate_in_fingerprint(self):
        default = self.config().fingerprint()
        explicit = self.config(search_interval_min_seconds=60, search_interval_max_seconds=90,
                               read_interval_min_seconds=10, read_interval_max_seconds=20)
        self.assertEqual(default, explicit.fingerprint())
        for field, value in (("search_interval_min_seconds", 65), ("search_interval_max_seconds", 95),
                             ("read_interval_min_seconds", 12), ("read_interval_max_seconds", 25)):
            with self.subTest(field=field):
                self.assertNotEqual(default, self.config(**{field: value}).fingerprint())

    def test_read_and_search_ranges_keep_original_limits(self):
        config = self.config(search_interval_min_seconds=30, search_interval_max_seconds=3600,
                             read_interval_min_seconds=3, read_interval_max_seconds=3600)
        self.assertEqual((config.search_min, config.search_max, config.read_min, config.read_max), (30, 3600, 3, 3600))
        for kind, low in (("search", 30), ("read", 3)):
            for minimum, maximum in ((low - 1, 60), (low, 3601), (60, 59), (True, 60), (low, False),
                                     (str(low), 60), (low, 60.0), (None, 60)):
                with self.subTest(kind=kind, minimum=minimum, maximum=maximum):
                    with self.assertRaisesRegex(ValueError, f"{kind} interval must be an integer range"):
                        self.config(**{f"{kind}_interval_min_seconds": minimum, f"{kind}_interval_max_seconds": maximum})
            for legacy in (low - 1, 3601, True, str(low), 60.0):
                with self.subTest(kind=kind, legacy=legacy):
                    with self.assertRaisesRegex(ValueError, f"{kind} interval must be an integer range"):
                        self.config(**{f"{kind}_interval_seconds": legacy})


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config_path = self.root / "config.json"
        self.config_data = {"schema_version": 1, "account_id": "test-account", "state_dir": "state", "output_dir": "output", "mcp_url": "http://127.0.0.1:1/mcp", "cdp_url": "http://127.0.0.1:1", "timeout_seconds": 1, "allow_write": True, "max_read_attempts": 3}
        self.now = [1000.0]
        self.config_path.write_text(json.dumps(self.config_data))
        ops.prepare_state(self.config_path)
        self.config = ops.Config(self.config_path)
        self.store = ops.Store(self.config, now=lambda: self.now[0])
        FakeMCP.calls = []; FakeMCP.records = {}; FakeMCP.outcome = "success"

    def tearDown(self):
        self.store.db.close(); self.tmp.cleanup()

    def fake(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeMCP)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        self.config.url = "http://127.0.0.1:%s/mcp" % server.server_port
        return ops.MCPTransport(self.config)

    def comment(self, **kwargs):
        return self.store.enqueue("post_comment_to_feed", {"feed_id": "fixture", "xsec_token": "test-only-token", "content": "原帖中的具体建议"}, "fixture-human-approval", **kwargs)["id"]

    def reviewed_manual_comment(self, content="原帖中的具体建议"):
        # Real local lifecycle with explicit synthetic artifacts; no platform IO.
        self.config.data["expected_user_id"] = "fixture-user"
        continuous = self.store.continuous
        continuous.dispatch("configure", {"authorization_ref": "SYNTHETIC TEST ONLY", "quality_standard": "offline content contract",
                             "allowed_tools": ["post_comment_to_feed"], "content_profile": content_fixture.profile(self.root)})
        run = continuous.dispatch("begin", {"plan_ref": "SYNTHETIC TEST ONLY", "plan": {
            "posts": 0, "comments": 1, "comment_strategy": {"A": 1, "B": 0, "C": 0}}})
        item = {"tool": "post_comment_to_feed", "payload": {"feed_id": "fixture", "content": content, "xsec_token": "test-only-token"}}
        content_fixture.register(continuous, run, [item])
        continuous.dispatch("review", {"run_id": run["id"], "payloads": [item], "evidence": "SYNTHETIC TEST ONLY: offline contract"})
        task_id = self.store.enqueue(item["tool"], item["payload"], "SYNTHETIC TEST ONLY: explicit direct authorization", run_id=run["id"])["id"]
        return run, task_id

    def direct_receipt(self, task_id, status="succeeded", fingerprint=None):
        # Independently generated by pinned Go encoding/json mutationSignature.
        return {"id": task_id, "tool": "post_comment_to_feed", "status": status,
                "fingerprint": fingerprint or "afc7e0ad948732e6dc791a2a548d878299ce2afc7dcfcac1e0e2e43d18a1b300",
                "updated_at": "2026-10-16T12:34:56.123456789+08:00", "result": {"feed_id": "fixture", "success": True},
                "evidence": "SYNTHETIC TEST ONLY: durable Go receipt fixture"}

    def test_direct_comment_receipt_accounts_real_task_preserves_unknown_and_is_idempotent(self):
        old = self.store.enqueue("post_comment_to_feed", {"feed_id": "old-target", "content": "older fixture"}, "fixture permission")["id"]
        self.store.finish(self.store.claim(), "unknown", "ERR_NETWORK")
        run, task_id = self.reviewed_manual_comment()
        receipt = self.direct_receipt(task_id)
        transport = ReceiptTransport(receipt)
        result = ops.reconcile(self.store, transport, task_id)
        self.assertTrue(result["receipt_imported"])
        current = self.store.task(task_id)
        self.assertEqual((current["status"], current["attempts"], current["auto_run"], current["run_id"]), ("succeeded", 1, 0, run["id"]))
        self.assertEqual(json.loads(current["result"]), receipt)
        self.assertEqual(self.store.task(old)["status"], "unknown")
        self.assertFalse(self.store.status()["runner_enabled"])
        event_count = self.store.db.execute("SELECT count(*) FROM events WHERE task_id=?", (task_id,)).fetchone()[0]
        self.assertTrue(ops.reconcile(self.store, transport, task_id)["duplicate"])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM events WHERE task_id=?", (task_id,)).fetchone()[0], event_count)
        self.assertEqual(self.store.task(task_id), current)
        # Even after review invalidation, the actual successful task uses its
        # original batch slot. A later fresh review cannot spend it twice.
        continuous = self.store.continuous
        continuous.dispatch("update", {"run_id": run["id"], "plan_ref": "same fixture scope", "plan": run["body"]["plan"]})
        extra = {"tool": "post_comment_to_feed", "payload": {"feed_id": "extra", "content": "extra synthetic comment"}}
        content_fixture.register(continuous, run, [extra])
        with self.assertRaisesRegex(ValueError, "ERR_CONTENT_PLAN_SCOPE"):
            continuous.dispatch("review", {"run_id": run["id"], "payloads": [extra], "evidence": "synthetic fixture"})
        self.assertIsNone(self.store.claim(automatic=True))
        self.assertEqual(transport.calls, [("get_operation_status", {"operation_id": task_id})] * 2)

    def test_direct_confirmed_receipt_matches_go_escaping_and_normal_bridge(self):
        _, task_id = self.reviewed_manual_comment('中文<>&\u2028\u2029"\\\n🙂')
        receipt = self.direct_receipt(task_id, "confirmed", "e61b2c6542666e8362d307d2365591d4279238ff29cca0d1ccd5c518f99a902b")
        transport = ReceiptTransport(receipt)
        with mock.patch.object(ops, "MCPTransport", return_value=transport):
            result = ops.bridge(self.config, {"action": "reconcile", "task_id": task_id})
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(json.loads(self.store.task(task_id)["result"]), receipt)
        self.assertFalse(self.store.status()["runner_enabled"])

    def test_direct_receipt_rejects_wrong_identity_fingerprint_target_or_date(self):
        _, task_id = self.reviewed_manual_comment()
        original = self.store.task(task_id)
        variants = [{}, {"id": "different-task"}, {"tool": "reply_comment_in_feed"}, {"fingerprint": original["fingerprint"]},
                    {"result": {"feed_id": "other-target", "success": True}}, {"result": {}}, {"result": None},
                    {"result": {"feed_id": "fixture"}}, {"result": {"feed_id": "fixture", "success": False}},
                    {"updated_at": "2026-10-16"}, {"updated_at": "not-a-dateZ"}]
        for change in variants:
            with self.subTest(change=change):
                receipt = {**self.direct_receipt(task_id), **change} if change else {}
                with self.assertRaisesRegex(ValueError, "ERR_RECEIPT_MISMATCH"):
                    ops.reconcile(self.store, ReceiptTransport(receipt), task_id)
                self.assertEqual(self.store.task(task_id), original)
        self.assertFalse(self.store.status()["runner_enabled"])

    def test_direct_unknown_receipt_preserves_all_evidence_without_resend(self):
        _, task_id = self.reviewed_manual_comment()
        receipt = self.direct_receipt(task_id, "unknown")
        receipt.pop("result")
        transport = ReceiptTransport(receipt)
        self.assertEqual(ops.reconcile(self.store, transport, task_id)["status"], "unknown")
        current = self.store.task(task_id)
        self.assertEqual((current["attempts"], current["server_id"], current["error_code"]), (1, task_id, "ERR_OPERATION_UNKNOWN"))
        self.assertEqual(json.loads(current["result"]), receipt)
        self.assertIsNone(self.store.claim())
        self.assertFalse(self.store.status()["runner_enabled"])
        self.assertEqual(transport.calls, [("get_operation_status", {"operation_id": task_id})])

    def test_direct_receipt_missing_error_and_nonterminal_leave_manual_task_unchanged(self):
        _, task_id = self.reviewed_manual_comment()
        original = self.store.task(task_id)
        for status in ("inflight", "not_applied", "failed"):
            with self.subTest(status=status):
                result = ops.reconcile(self.store, ReceiptTransport(self.direct_receipt(task_id, status)), task_id)
                self.assertEqual(result["status"], "queued")
                self.assertEqual(self.store.task(task_id), original)
        for code in ("ERR_OPERATION_NOT_FOUND", "ERR_NETWORK"):
            with self.subTest(code=code), self.assertRaises(ops.ToolFailure):
                ops.reconcile(self.store, ReceiptTransport(error=ops.ToolFailure(code)), task_id)
            self.assertEqual(self.store.task(task_id), original)
        self.assertFalse(self.store.status()["runner_enabled"])

    def test_direct_receipt_requires_unowned_manual_task_and_original_run_approval(self):
        _, task_id = self.reviewed_manual_comment()
        original = self.store.task(task_id)
        for field, value in (("owner", "other-worker"), ("lease_until", 123.0), ("auto_run", 1), ("attempts", 1), ("approval_ref", ""),
                             ("run_id", None), ("run_id", "nonexistent"), ("tool", "reply_comment_in_feed"),
                             ("payload", json.dumps({"feed_id": "fixture", "content": "原帖中的具体建议", "operation_id": "other"}))):
            with self.subTest(field=field, value=value):
                self.store.db.execute("UPDATE tasks SET " + field + "=? WHERE id=?", (value, task_id))
                transport = ReceiptTransport(self.direct_receipt(task_id))
                with self.assertRaises(ValueError):
                    ops.reconcile(self.store, transport, task_id)
                self.assertEqual(transport.calls, [])
                self.assertEqual(self.store.task(task_id)[field], value)
                self.store.db.execute("UPDATE tasks SET " + field + "=? WHERE id=?", (original[field], task_id))

    def test_direct_receipt_cas_does_not_overwrite_changed_task(self):
        _, task_id = self.reviewed_manual_comment()
        for field, value in (("owner", "another-worker"), ("lease_until", 2000.0)):
            with self.subTest(field=field):
                transport = ReceiptTransport(self.direct_receipt(task_id), before_return=lambda:
                    self.store.db.execute("UPDATE tasks SET " + field + "=? WHERE id=?", (value, task_id)))
                with self.assertRaisesRegex(ValueError, "ERR_RECEIPT_TASK_CHANGED"):
                    ops.reconcile(self.store, transport, task_id)
                current = self.store.task(task_id)
                self.assertEqual((current["status"], current["attempts"], current[field]), ("queued", 0, value))
                self.assertIsNone(current["result"])
                self.store.db.execute("UPDATE tasks SET " + field + "=NULL WHERE id=?", (task_id,))

    def test_duplicate_ignores_rotating_token(self):
        one = self.comment()
        two = self.store.enqueue("post_comment_to_feed", {"feed_id": "fixture", "xsec_token": "rotated", "content": "原帖中的具体建议"})
        self.assertEqual(one, two["id"]); self.assertTrue(two["duplicate"])

    def test_defaults_disable_writes_and_preserve_read_work(self):
        self.config.allow_write = False
        write = self.comment(); read = self.store.enqueue("check_login_status", {})["id"]
        result = ops.tick(self.store, self.fake())
        self.assertEqual(result["id"], read)
        self.assertEqual(self.store.task(write)["status"], "blocked")

    def test_missing_human_approval_never_calls_write_tool(self):
        task = self.store.enqueue("post_comment_to_feed", {"feed_id": "f", "content": "c"})["id"]
        ops.tick(self.store, self.fake())
        self.assertEqual(self.store.task(task)["status"], "blocked")
        self.assertEqual(FakeMCP.calls, [])

    def test_restart_recovers_inflight_write_as_unknown(self):
        task = self.comment(); claimed = self.store.claim()
        self.now[0] = claimed["lease_until"] + 1
        restarted = ops.Store(self.config, now=lambda: self.now[0]); self.addCleanup(restarted.db.close)
        restarted.claim()
        self.assertEqual(restarted.task(task)["status"], "unknown")
        self.assertTrue(restarted.status()["alerts"])

    def test_restart_recovers_read_with_bounded_attempts(self):
        task = self.store.enqueue("search_feeds", {"keyword": "fixture"})["id"]
        for i in range(3):
            claimed = self.store.claim(); self.now[0] = claimed["lease_until"] + 1
        self.store.claim()
        self.assertEqual(self.store.task(task)["status"], "failed")

    def test_network_interruption_is_unknown_for_write(self):
        task = self.comment(); FakeMCP.outcome = "network"
        ops.tick(self.store, self.fake())
        self.assertEqual(self.store.task(task)["status"], "unknown")
        self.assertEqual(len(FakeMCP.calls), 1)
        ops.tick(self.store, self.fake())
        self.assertEqual(len(FakeMCP.calls), 1)

    def test_read_network_retries_are_finite_and_scheduled(self):
        task = self.store.enqueue("search_feeds", {"keyword": "fixture"})["id"]
        FakeMCP.outcome = "network"; transport = self.fake()
        for i in range(3):
            ops.tick(self.store, transport); self.now[0] += 100
        self.assertEqual(self.store.task(task)["status"], "failed")
        self.assertEqual(len(FakeMCP.calls), 3)

    def test_captcha_is_blocker_not_logout_or_success(self):
        task = self.comment(); FakeMCP.outcome = "ERR_CAPTCHA: needs human"
        ops.tick(self.store, self.fake())
        self.assertEqual(self.store.task(task)["status"], "blocked")
        self.assertEqual(self.store.task(task)["error_code"], "ERR_CAPTCHA")

    def test_browser_unavailable_pauses_reads_after_bounded_retries_across_restart(self):
        task = self.store.enqueue("check_login_status", {})["id"]
        FakeMCP.outcome = "ERR_BROWSER_UNAVAILABLE: preserve session"
        transport = self.fake()
        for _ in range(3):
            ops.tick(self.store, transport)
            self.now[0] += 60
        self.assertEqual(self.store.task(task)["status"], "blocked")
        restarted = ops.Store(self.config, now=lambda: self.now[0])
        self.addCleanup(restarted.db.close)
        self.assertTrue(restarted.status()["account_blocked"])
        self.assertIsNone(restarted.claim())

    def test_browser_disconnect_is_not_no_submit_evidence(self):
        task = self.comment()
        FakeMCP.outcome = "ERR_BROWSER_UNAVAILABLE: connection lost"
        ops.tick(self.store, self.fake())
        self.assertEqual(self.store.task(task)["status"], "unknown")

    def test_comment_before_submit_blocks_without_becoming_unknown(self):
        task = self.comment(); FakeMCP.outcome = "ERR_COMMENT_NOT_SUBMITTED: phase=input; operation_id=" + task
        transport = self.fake(); ops.tick(self.store, transport); ops.tick(self.store, transport)
        self.assertEqual(self.store.task(task)["status"], "blocked")
        self.assertEqual(self.store.task(task)["error_code"], "ERR_COMMENT_NOT_SUBMITTED")
        self.assertEqual(len(FakeMCP.calls), 1)

    def test_captcha_inside_comment_pre_submit_stops_account(self):
        task = self.comment(); FakeMCP.outcome = "ERR_COMMENT_NOT_SUBMITTED: phase=page_check: ERR_CAPTCHA"
        transport = self.fake(); ops.tick(self.store, transport)
        read = self.store.enqueue("search_feeds", {"keyword": "later"})["id"]
        ops.tick(self.store, transport)
        self.assertEqual(self.store.task(task)["error_code"], "ERR_CAPTCHA")
        self.assertEqual(len(FakeMCP.calls), 1)
        self.assertNotEqual(self.store.task(read)["status"], "succeeded")

    def test_confirmed_pre_submit_page_timeout_retries_are_finite(self):
        task = self.comment(); FakeMCP.outcome = "ERR_COMMENT_NOT_SUBMITTED: phase=page_ready: ERR_COMMENT_TEMPORARY_PAGE_FAILURE"
        transport = self.fake()
        for attempt in range(3):
            ops.tick(self.store, transport)
            if attempt < 2:
                self.assertEqual(self.store.task(task)["status"], "queued")
                ops.tick(self.store, transport)
                self.assertEqual(len(FakeMCP.calls), attempt + 1)
            self.now[0] += 181
        self.assertEqual(self.store.task(task)["status"], "blocked")
        self.assertEqual(len(FakeMCP.calls), 3)

    def test_quotas_permissions_and_expiry_block(self):
        for code in ("ERR_QUOTA", "ERR_PERMISSION", "ERR_NOT_LOGGED_IN", "ERR_ACCOUNT_UNCONFIRMED"):
            task = self.store.enqueue("search_feeds", {"keyword": code})["id"]
            FakeMCP.outcome = code; ops.tick(self.store, self.fake())
            self.assertEqual(self.store.task(task)["status"], "blocked")
            self.store.db.execute("DELETE FROM meta WHERE key='account_blocked'")

    def test_partial_failure_and_dependency(self):
        first = self.store.enqueue("search_feeds", {"keyword": "first"})["id"]
        second = self.store.enqueue("get_feed_detail", {"feed_id": "b"}, dependency=first)["id"]
        transport = self.fake(); FakeMCP.outcome = "ERR_PERMISSION"
        ops.tick(self.store, transport); self.assertIsNone(self.store.claim())
        self.store.resume(first, "fixture-permission-restored")
        FakeMCP.outcome = "success"; ops.tick(self.store, transport); ops.tick(self.store, transport)
        self.assertEqual(self.store.task(second)["status"], "succeeded")

    def test_known_success_and_duplicate_trigger_execute_once(self):
        task = self.comment(); transport = self.fake()
        ops.tick(self.store, transport); self.comment(); ops.tick(self.store, transport)
        self.assertEqual(len(FakeMCP.calls), 1)
        self.assertEqual(self.store.task(task)["status"], "succeeded")

    def test_unknown_cannot_resume_or_blind_retry(self):
        task = self.comment(); claimed = self.store.claim()
        self.store.finish(claimed, "unknown", "ERR_TIMEOUT")
        with self.assertRaises(ValueError): self.store.resume(task, "try again")
        transport = self.fake(); self.assertEqual(ops.reconcile(self.store, transport, task)["status"], "unknown")
        FakeMCP.records[task] = {"status": "succeeded", "result": {"verified": True}}
        self.assertEqual(ops.reconcile(self.store, transport, task)["status"], "succeeded")
        self.assertTrue(all(c["name"] == "get_operation_status" for c in FakeMCP.calls))

    def test_known_not_applied_resolution_allows_one_retry(self):
        task = self.comment(); claimed = self.store.claim(); self.store.finish(claimed, "unknown", "ERR_TIMEOUT")
        FakeMCP.records[task] = {"status": "not_applied", "evidence": "fixture verified absent"}
        ops.reconcile(self.store, self.fake(), task)
        self.now[0] += 181; ops.tick(self.store, self.fake())
        self.assertEqual(self.store.task(task)["status"], "succeeded")

    def test_unknown_stops_automatic_queue_for_other_targets(self):
        original = self.comment(); claimed = self.store.claim()
        self.store.finish(claimed, "unknown", "ERR_TIMEOUT")
        another = self.store.enqueue("post_comment_to_feed", {"feed_id": "other", "content": "new target"}, "approved")["id"]
        self.now[0] += 181
        self.assertIsNone(self.store.claim(automatic=True))
        self.assertEqual(self.store.task(original)["status"], "unknown")
        self.assertEqual(self.store.task(another)["status"], "queued")
        self.assertEqual(self.store.task(another)["attempts"], 0)

    def test_two_workers_share_lease_and_write_interval(self):
        first = self.comment(); second = self.store.enqueue("post_comment_to_feed", {"feed_id": "b", "content": "different"}, "approved")["id"]
        other = ops.Store(self.config, now=lambda: self.now[0]); self.addCleanup(other.db.close)
        claimed = self.store.claim(); self.assertIsNone(other.claim())
        # The real browser executor owns/draws this shared deadline, before
        # returning a result. The queue does not invent a second cooldown.
        path = self.config.state_dir / "runtime-state.json"
        data = json.loads(path.read_text())
        data["next_write_at"] = ops.dt.datetime.fromtimestamp(self.now[0] + 150, ops.dt.timezone.utc).isoformat()
        ops.atomic_file(path, json.dumps(data).encode())
        self.store.finish(claimed, "succeeded", result={"verified": True})
        self.assertIsNone(self.store.db.execute("SELECT value FROM meta WHERE key='next_write_at'").fetchone())
        self.assertIsNone(other.claim()); self.now[0] += 181
        self.assertEqual(other.claim()["id"], second)

    def test_definitive_failed_dependency_skips_queued_descendants_only(self):
        first = self.store.enqueue("search_feeds", {"keyword": "first"})["id"]
        second = self.store.enqueue("get_feed_detail", {"feed_id": "second"}, dependency=first, not_before=999999)["id"]
        third = self.store.enqueue("get_feed_detail", {"feed_id": "third"}, dependency=second)["id"]
        independent = self.store.enqueue("get_runtime_status", {})["id"]
        self.store.db.execute("UPDATE tasks SET status='failed' WHERE id=?", (first,))
        self.assertEqual(self.store.claim()["id"], independent)
        for task_id in (second, third):
            self.assertEqual(self.store.task(task_id)["status"], "cancelled")
            self.assertEqual(self.store.task(task_id)["error_code"], "ERR_DEPENDENCY_FAILED")

    def test_pre_submit_retry_reuses_executor_deadline_without_fixed_wait(self):
        task_id = self.comment()
        path = self.config.state_dir / "runtime-state.json"
        data = json.loads(path.read_text())
        class TemporaryPage:
            def call(_, tool, payload):
                data["next_write_at"] = ops.dt.datetime.fromtimestamp(self.now[0]+70, ops.dt.timezone.utc).isoformat()
                ops.atomic_file(path, json.dumps(data).encode())
                raise ops.ToolFailure("ERR_COMMENT_TEMPORARY_PAGE_FAILURE")
        result = ops.tick(self.store, TemporaryPage())
        self.assertEqual(result["next_at"], self.now[0]+70)
        self.assertEqual(self.store.task(task_id)["status"], "queued")
        self.assertIsNone(self.store.db.execute("SELECT value FROM meta WHERE key='next_write_at'").fetchone())

    def test_lost_worker_cannot_overwrite_unknown(self):
        task = self.comment(); claimed = self.store.claim(); self.now[0] = claimed["lease_until"] + 1
        self.store.claim()
        with self.assertRaises(ValueError): self.store.finish(claimed, "succeeded")
        self.assertEqual(self.store.task(task)["status"], "unknown")

    def test_state_corruption_and_account_mismatch_fail_explicitly(self):
        data = dict(self.config_data, account_id="other")
        self.config_path.write_text(json.dumps(data))
        with self.assertRaises(ValueError): ops.Store(ops.Config(self.config_path))
        bad = self.root / "bad"; bad.mkdir(); (bad / "tasks.sqlite3").write_bytes(b"broken")
        self.config_path.write_text(json.dumps(dict(self.config_data, state_dir="bad")))
        with self.assertRaises(Exception): ops.Store(ops.Config(self.config_path))

    def test_asset_content_participates_in_deduplication(self):
        image = self.root / "fixture.png"; image.write_bytes(b"one")
        payload = {"title": "fixture", "content": "fixture", "images": [str(image)]}
        first = self.store.enqueue("publish_content", payload)["id"]
        image.write_bytes(b"two")
        second = self.store.enqueue("publish_content", payload)["id"]
        self.assertNotEqual(first, second)

    def test_cli_e2e_with_fake_http_mcp(self):
        self.fake(); self.config_data["mcp_url"] = self.config.url; self.config_data["expected_state_id"] = self.config.state_id; self.config_path.write_text(json.dumps(self.config_data))
        payload = self.root / "payload.json"; payload.write_text(json.dumps({"keyword": "fixture"}))
        prefix = [sys.executable, str(ROOT / "runtime/xhs_ops.py"), "--config", str(self.config_path)]
        result = json.loads(subprocess.check_output(prefix + ["enqueue", "--tool", "search_feeds", "--payload", str(payload)]))
        finished = json.loads(subprocess.check_output(prefix + ["tick"]))
        self.assertEqual(result["id"], finished["id"]); self.assertEqual(finished["status"], "succeeded")

    def test_private_store_and_logs_omit_tokens(self):
        self.comment(); ops.tick(self.store, self.fake())
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("test-only-token", json.dumps(self.store.status()))

    def test_lost_bound_state_is_not_recreated(self):
        (self.config.state_dir / "runtime-state.json").unlink()
        with self.assertRaisesRegex(ValueError, "ERR_STATE_UNCONFIRMED"): self.store.claim()
        with self.assertRaises(ValueError): ops.prepare_state(self.config_path)
        self.assertFalse((self.config.state_dir / "runtime-state.json").exists())

    def test_lost_bound_queue_is_not_recreated(self):
        self.store.db.close()
        self.store.path.unlink()
        with self.assertRaisesRegex(ValueError, "ERR_STATE_LOST"): ops.Store(self.config)
        self.assertFalse(self.store.path.exists())

    def test_account_blocker_halts_new_browser_actions(self):
        first = self.comment()
        self.store.enqueue("search_feeds", {"keyword": "later"})
        FakeMCP.outcome = "ERR_CAPTCHA"; transport = self.fake()
        ops.tick(self.store, transport); ops.tick(self.store, transport)
        self.assertEqual(len(FakeMCP.calls), 1)
        self.assertTrue(self.store.status()["account_blocked"])
        self.store.resume(first, "fixture verified manually", "approved")
        self.assertFalse(self.store.status()["account_blocked"])

    def test_rate_deferral_preserves_failure_budget_until_due(self):
        task = self.comment(); transport = self.fake(); FakeMCP.outcome = "ERR_RATE_LIMIT"
        for _ in range(3):
            ops.tick(self.store, transport); self.now[0] += 181
        self.assertEqual(self.store.task(task)["status"], "queued")
        self.assertEqual(self.store.task(task)["attempts"], 0)
        self.assertEqual(len(FakeMCP.calls), 3)
        FakeMCP.outcome = "success"
        ops.tick(self.store, transport)
        self.assertEqual(self.store.task(task)["status"], "succeeded")
        self.assertEqual(FakeMCP.calls[-1]["arguments"]["operation_id"], task)

    def test_unrelated_unknown_operation_is_never_counted_as_current_success(self):
        task = self.comment(); transport = self.fake()
        FakeMCP.outcome = "ERR_UNRESOLVED_OPERATION: operation_id=other-task"
        ops.tick(self.store, transport)
        self.assertEqual(self.store.task(task)["status"], "blocked")
        FakeMCP.records["other-task"] = {"status": "succeeded"}
        with self.assertRaises(ValueError): ops.reconcile(self.store, transport, task)
        self.assertEqual(len(FakeMCP.calls), 1)


if __name__ == "__main__": unittest.main()
