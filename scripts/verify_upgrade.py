#!/usr/bin/env python3
"""Actual isolated service upgrade/rollback. No browser, account or site calls."""
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def verify(previous):
    previous = previous.resolve()
    previous_version = (previous / "VERSION").read_text().strip()
    if previous_version not in {"0.3.6", "0.4.0", "0.4.7", "0.4.8", "0.4.9", "0.4.10", "0.4.11", "0.5.0", "0.5.1", "0.5.2", "0.5.3", "0.5.4", "0.5.5", "0.5.6", "0.5.7", "0.5.8", "0.5.9", "0.5.10", "0.5.11", "0.5.12"}:
        raise ValueError("use an explicitly supported prior package (0.3.6, 0.4.0, 0.4.7–0.4.11 or 0.5.0–0.5.12)")
    spec = importlib.util.spec_from_file_location("upgrade_ops", ROOT / "runtime/xhs_ops.py")
    ops = importlib.util.module_from_spec(spec); spec.loader.exec_module(ops)
    steps = []
    with tempfile.TemporaryDirectory(prefix="xhs-upgrade-check-") as directory:
        base = Path(directory)
        port = free_port()
        private = base / "config.json"
        data = {"schema_version": 1, "account_id": "offline-upgrade-fixture", "expected_user_id": "fixture-user",
                "state_dir": str(base / "state"), "output_dir": str(base / "output"), "allow_write": False,
                "mcp_binary": str(previous / "bin/xhs-mcp-custom"), "listen": "127.0.0.1:%d" % port,
                "mcp_url": "http://127.0.0.1:%d/mcp" % port, "cdp_url": "http://127.0.0.1:1",
                "auth_token_env": "XHS_UPGRADE_UNUSED_TOKEN", "allow_reconcile": True}
        private.write_text(json.dumps(data))
        def command(root, *args):
            process = subprocess.run([sys.executable, str(root / "runtime/xhs_ops.py"), "--config", str(private), *args], capture_output=True, text=True, timeout=40)
            if process.returncode:
                raise AssertionError(process.stderr[-1500:])
            return json.loads(process.stdout)
        def switch(root):
            data = json.loads(private.read_text()); data["mcp_binary"] = str(root / "bin/xhs-mcp-custom")
            private.write_text(json.dumps(data))
        def operation(root, action, data):
            input_path = base / "operation.json"
            input_path.write_text(json.dumps(data))
            return command(root, "operation-run", action, "--data", str(input_path))
        def record(phase):
            cfg = ops.Config(private)
            request = ops.urllib.request.Request(cfg.url.rsplit("/mcp", 1)[0] + "/runtime")
            with ops.urllib.request.urlopen(request, timeout=3) as response:
                actual = json.load(response)["data"]
            steps.append({"phase": phase, "version": actual["version"], "state_id": actual["state_id"], "write_enabled": actual["write_enabled"]})
            assert not actual["write_enabled"]
            return actual
        current = previous
        try:
            command(previous, "init-state")
            operation(previous, "configure", {"authorization_ref": "offline fixture", "quality_standard": "fixture only", "allowed_tools": ["publish_content"]})
            run = operation(previous, "begin", {"plan_ref": "offline fixture", "plan": {"posts": 3}})
            first_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)
            initial_slots = operation(previous, "schedule", {"run_id": run["id"], "count": 3, "first_at": first_at.isoformat()})["schedule_slots"]
            payload = base / "payload.json"; payload.write_text("{}")
            task = command(previous, "enqueue", "--tool", "get_runtime_status", "--payload", str(payload))
            state = base / "state/runtime-state.json"
            value = json.loads(state.read_text())
            value["operations"] = {
                "old-success": {"id": "old-success", "tool": "comment", "fingerprint": "old", "status": "succeeded", "updated_at": "2026-01-01T00:00:00Z", "result": {"success": True}},
                "fixture-unresolved": {"id": "fixture-unresolved", "tool": "comment", "fingerprint": "new", "status": "unknown", "updated_at": "2026-01-01T00:00:00Z"}}
            value["next_write_at"] = "2030-01-01T00:00:00Z"
            state.write_text(json.dumps(value))
            command(previous, "service", "ensure")
            old = record("prior-started")
            command(previous, "service", "stop")
            switch(ROOT); current = ROOT
            command(ROOT, "service", "ensure")
            upgraded = record("candidate-started")
            assert upgraded["state_id"] == old["state_id"]
            assert json.loads(state.read_text())["version"] == 2
            transport = ops.MCPTransport(ops.Config(private))
            assert transport.call("get_task_status", {"task_id": task["id"]})["status"] == "queued"
            assert operation(ROOT, "schedule", {"run_id": run["id"]})["schedule_slots"] == initial_slots
            assert transport.call("get_operation_status", {"operation_id": "old-success"})["status"] == "succeeded"
            # Old batches remain readable, but legacy self-reported quality
            # booleans cannot authorize content in the new runtime. A pending
            # profile is allowed during upgrade without losing the original run.
            content_checks = {}
            candidate_store = ops.Store(ops.Config(private))
            try:
                continuous = candidate_store.continuous
                legacy_item = {"tool": "publish_content", "payload": {
                    "title": "SYNTHETIC TEST ONLY", "content": "offline upgrade fixture",
                    "images": [], "schedule_at": initial_slots[0]}}
                legacy_review = {"run_id": run["id"], "payloads": [legacy_item],
                                 "evidence": "SYNTHETIC TEST ONLY: no actual content review",
                                 "checks": {k: True for k in ("sources_verified", "facts_checked", "humanizer_checked",
                                                             "useful_and_original", "targets_checked", "images_opened")}}
                try:
                    continuous.dispatch("review", legacy_review)
                except ValueError as error:
                    assert str(error).startswith("ERR_CONTENT_PROFILE_CONFIRMATION_REQUIRED"), str(error)
                else:
                    raise AssertionError("legacy quality booleans bypassed the current content contract")
                fixture_spec = importlib.util.spec_from_file_location("upgrade_content_fixture", ROOT / "tests/content_fixture.py")
                fixture = importlib.util.module_from_spec(fixture_spec); fixture_spec.loader.exec_module(fixture)
                pending_profile = fixture.profile(base)
                pending_profile["confirmation_sources"].pop("voice_samples")
                pending = continuous.dispatch("configure", {"content_profile": pending_profile})
                assert pending["active_run"]["id"] == run["id"]
                assert "voice_samples" in pending["content_profile_pending"]
                assert continuous.dispatch("schedule", {"run_id": run["id"]})["schedule_slots"] == initial_slots
                content_checks = {"legacy_quality_flags_rejected": True, "pending_profile_preserved": True,
                                  "profile_version": pending["content_profile_version"], "original_run_preserved": True}
            finally:
                candidate_store.db.close()
            # A local fixture resolution creates a new post-upgrade journal
            # result. No platform evidence or real task is fabricated.
            resolved = transport.call("resolve_operation", {"operation_id": "fixture-unresolved", "status": "confirmed", "evidence": "offline fixture only; no site operation"})
            assert resolved["status"] == "confirmed"
            command(ROOT, "service", "stop")
            if previous_version == "0.3.6":
                command(ROOT, "service", "export-legacy")
                exported = json.loads(state.read_text())
                assert exported["version"] == 1 and exported["operations"]["fixture-unresolved"]["status"] == "confirmed"
            else:
                exported = json.loads(state.read_text())
                assert exported["version"] == 2  # Direct rollback keeps the current indexed journal.
            assert ops.parse_timestamp(exported["next_write_at"]) == ops.parse_timestamp(value["next_write_at"])
            switch(previous); current = previous
            command(previous, "service", "ensure")
            rollback = record("prior-restored-with-current-receipts")
            assert rollback["state_id"] == old["state_id"]
            assert ops.MCPTransport(ops.Config(private)).call("get_operation_status", {"operation_id": "fixture-unresolved"})["status"] == "confirmed"
            # The old implementation inserts two columns positionally. This
            # exercises the calendar downgrade with real nonempty batch data.
            rollback_slots = operation(previous, "schedule", {"run_id": run["id"], "replace_unsubmitted": True, "count": 2,
                "first_at": (first_at+dt.timedelta(hours=8)).isoformat()})["schedule_slots"]
            command(previous, "service", "stop")
            switch(ROOT); current = ROOT
            command(ROOT, "service", "ensure")
            last = record("candidate-reapplied")
            assert last["operations"]["confirmed"] == 1 and last["operations"]["succeeded"] == 1
            assert operation(ROOT, "schedule", {"run_id": run["id"]})["schedule_slots"] == rollback_slots
            assert ops.MCPTransport(ops.Config(private)).call("get_task_status", {"task_id": task["id"]})["status"] == "queued"
            reopened = ops.Store(ops.Config(private))
            try:
                restored_content = reopened.continuous.dispatch("status", {})
                assert restored_content["content_profile_version"] == content_checks["profile_version"]
                assert "voice_samples" in restored_content["content_profile_pending"]
            finally:
                reopened.db.close()
        finally:
            command(current, "service", "stop")
    return {"status": "passed", "previous_version": previous_version, "version": (ROOT / "VERSION").read_text().strip(),
            "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(), "steps": steps,
            "candidate_binary_sha256": hashlib.sha256((ROOT / "bin/xhs-mcp-custom").read_bytes()).hexdigest(),
            "previous_binary_sha256": hashlib.sha256((previous / "bin/xhs-mcp-custom").read_bytes()).hexdigest(),
            "content_review_sha256": hashlib.sha256((ROOT / "runtime/content_review.py").read_bytes()).hexdigest(),
            "content_contract": content_checks,
            "calendar_and_queue_preserved": True, "real_site_actions": 0, "cloud_verified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.previous_root)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
