#!/usr/bin/env python3
"""可恢复的单步任务执行器。无常驻调度、无模型依赖、默认禁止写操作。"""
import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import secrets
import signal
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
import urllib.parse
import urllib.error
import urllib.request
import uuid

_MANAGED = {}
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
VERSION = (ROOT / "VERSION").read_text().strip()
WRITES = {"post_comment_to_feed", "reply_comment_in_feed", "publish_content", "publish_with_video"}
READS = {"get_runtime_status", "check_login_status", "search_feeds", "list_feeds", "get_feed_detail", "user_profile", "get_my_profile", "get_operation_status"}
SITE_READS = {"search_feeds", "list_feeds", "get_feed_detail", "user_profile", "get_my_profile"}
BLOCKERS = {"ERR_BROWSER_UNAVAILABLE", "ERR_CAPTCHA", "ERR_NOT_LOGGED_IN", "ERR_LOGIN_STATE_UNKNOWN", "ERR_ACCOUNT_UNCONFIRMED", "ERR_WRITE_NOT_ALLOWED", "ERR_PERMISSION", "ERR_QUOTA", "ERR_AUTH", "ERR_STATE_WRITE", "ERR_STATE_NOT_INITIALIZED", "ERR_STATE_UNCONFIRMED", "ERR_STATE_LOST", "ERR_UNRESOLVED_OPERATION"}
# 浏览器失联不能独立证明写入未提交；写入仍按 unknown 保守处理。
SAFE_NO_SUBMIT = (BLOCKERS - {"ERR_BROWSER_UNAVAILABLE"}) | {"ERR_MCP_NOT_DISPATCHED", "ERR_RATE_LIMIT", "ERR_RATE_LIMITED", "ERR_IDEMPOTENCY_CONFLICT", "ERR_INVALID_OPERATION_ID", "ERR_COMMENT_NOT_SUBMITTED", "ERR_COMMENT_TEMPORARY_PAGE_FAILURE"}
TRANSIENT = {"ERR_MCP_NOT_DISPATCHED", "ERR_LOGIN_STATE_UNKNOWN", "ERR_NETWORK_OR_PROTOCOL", "ERR_TIMEOUT", "ERR_COMMENT_TEMPORARY_PAGE_FAILURE", "ERR_BROWSER_NOT_CONNECTED"}
SAFE_NO_SUBMIT.update({"ERR_BROWSER_NOT_CONNECTED", "ERR_ACTION_NOT_SUBMITTED"})


def bounded_log_lines(stream, limit=65536):
    """Drop whole oversized lines: fragments could split a credential marker."""
    while True:
        raw = stream.readline(limit)
        if not raw:
            return
        if len(raw) == limit and not raw.endswith(b"\n"):
            while raw and not raw.endswith(b"\n"):
                raw = stream.readline(limit)
            yield b"ERR_LOG_LINE_TOO_LARGE: content omitted\n"
        else:
            yield raw


def parse_timestamp(value):
    # Go writes nanoseconds; Python 3.9 only accepts microseconds here.
    value = re.sub(r"(\.[0-9]{6})[0-9]+", r"\1", value)
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


class Config:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.data = json.loads(self.path.read_text())
        if self.data.get("schema_version") != 1:
            raise ValueError("unsupported config schema")
        self.account = self.data.get("account_id", "")
        if not self.account or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", self.account):
            raise ValueError("account_id required (letters, digits, _ or -)")
        self.state_dir = self.resolve(self.data["state_dir"])
        self.output_dir = self.resolve(self.data["output_dir"])
        self.url = self.data["mcp_url"].rstrip("/")
        parsed = urllib.parse.urlparse(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("invalid mcp_url; credentials must be provided by environment")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("remote MCP requires HTTPS")
        self.timeout = int(self.data.get("timeout_seconds", 150))
        if not 1 <= self.timeout <= 180:
            raise ValueError("timeout_seconds must be 1..180")
        self.lease = self.timeout + 45
        self.max_attempts = int(self.data.get("max_read_attempts", 3))
        if not 1 <= self.max_attempts <= 5:
            raise ValueError("max_read_attempts must be 1..5")
        legacy_interval = self.data.get("write_interval_seconds")
        self.write_min = self.data.get("write_interval_min_seconds", legacy_interval if legacy_interval is not None else 60)
        self.write_max = self.data.get("write_interval_max_seconds", legacy_interval if legacy_interval is not None else 180)
        if type(self.write_min) is not int or type(self.write_max) is not int or not 60 <= self.write_min <= self.write_max <= 3600:
            raise ValueError("write interval must be an integer range within 60..3600 seconds")
        self.write_interval = self.write_max
        for kind, default_min, default_max, low in (("search", 60, 90, 30), ("read", 10, 20, 3)):
            legacy_interval = self.data.get(f"{kind}_interval_seconds")
            interval_min = self.data.get(f"{kind}_interval_min_seconds", legacy_interval if legacy_interval is not None else default_min)
            interval_max = self.data.get(f"{kind}_interval_max_seconds", legacy_interval if legacy_interval is not None else default_max)
            if type(interval_min) is not int or type(interval_max) is not int or not low <= interval_min <= interval_max <= 3600:
                raise ValueError(f"{kind} interval must be an integer range within {low}..3600 seconds")
            setattr(self, f"{kind}_min", interval_min)
            setattr(self, f"{kind}_max", interval_max)
        self.allow_write = self.data.get("allow_write", False) is True
        self.state_id = self.data.get("expected_state_id", "")
        self.token = os.getenv(self.data.get("auth_token_env", "XHS_MCP_TOKEN"), "")
        self.recovery_limit = int(self.data.get("max_auto_recoveries", 2))
        if not 0 <= self.recovery_limit <= 3:
            raise ValueError("max_auto_recoveries must be 0..3")
        cache_seconds = self.data.get("read_cache_seconds", 30)
        if type(cache_seconds) is not int or not 0 <= cache_seconds <= 120:
            raise ValueError("read_cache_seconds must be an integer in 0..120")
        # Removed browser-page reuse is intentionally ignored in older private
        # configurations. It no longer changes runtime behaviour or identity.
        for name, default, low, high in (("search_max_per_hour", 20, 1, 60), ("search_cache_seconds", 900, 0, 3600), ("detail_cache_seconds", 900, 0, 3600), ("read_max_per_hour", 120, 1, 360)):
            value = self.data.get(name, default)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} must be an integer in {low}..{high}")
        operations = self.data.get("operations", {})
        if not isinstance(operations, dict):
            raise ValueError("operations must be an object")
        retention = self.data.get("retention", {})
        if not isinstance(retention, dict):
            raise ValueError("retention must be an object")
        for key, default, low, high in (("managed_disk_limit_mb", 256, 32, 4096), ("abandoned_keep_hours", 72, 0, 168)):
            value = retention.get(key, default)
            if type(value) is not int or not low <= value <= high:
                raise ValueError("invalid retention setting: " + key)
        for key, default, low, high in (("read_keep_hours", 24, 1, 168), ("write_detail_keep_days", 7, 1, 30)):
            value = retention.get(key, default)
            if type(value) is not int or not low <= value <= high:
                raise ValueError("invalid retention setting: " + key)
        resources = self.data.get("resources", {})
        if not isinstance(resources, dict):
            raise ValueError("resources must be an object")
        self.resources = {}
        for key, default, low, high in (("go_memory_limit_mb", 256, 128, 1024), ("go_max_procs", 2, 1, 4),
                ("state_disk_limit_mb", 512, 64, 4096), ("min_free_disk_mb", 2048, 64, 16384),
                ("min_free_inodes", 1024, 128, 65536)):
            value = resources.get(key, default)
            if type(value) is not int or not low <= value <= high:
                raise ValueError("invalid resource setting: " + key)
            self.resources[key] = value

    def draw_write_interval(self):
        return self.write_min + secrets.randbelow(self.write_max - self.write_min + 1)

    def runtime_env(self):
        """Single translation from private settings to effective Go settings."""
        values = {"XHS_STATE_DIR": str(self.state_dir), "XHS_ACCOUNT_ID": self.account,
                  "XHS_INSTANCE_LOCK": str(self.state_dir / "xhs-mcp-custom.lock"),
                  "XHS_CDP_URL": self.data["cdp_url"], "XHS_ALLOW_WRITE": "1" if self.allow_write else "0",
                  "XHS_EXPECTED_USER_ID": self.data.get("expected_user_id", ""), "AUTH_TOKEN": self.token,
                  "XHS_EXPECTED_STATE_ID": self.state_id, "XHS_LOGIN_PROBE_INTERVAL": "0", "XHS_EXPORT_COOKIES": "0",
                  "XHS_CAPTCHA_COOLDOWN": self.data.get("captcha_cooldown", "15m"),
                  "XHS_PLATFORM_RATE_LIMIT_COOLDOWN": self.data.get("platform_rate_limit_cooldown", "1h"),
                  "XHS_WRITE_INTERVAL_MIN": str(self.write_min)+"s", "XHS_WRITE_INTERVAL_MAX": str(self.write_max)+"s",
                  "XHS_SEARCH_INTERVAL_MIN": str(self.search_min)+"s", "XHS_SEARCH_INTERVAL_MAX": str(self.search_max)+"s",
                  "XHS_READ_INTERVAL_MIN": str(self.read_min)+"s", "XHS_READ_INTERVAL_MAX": str(self.read_max)+"s",
                  # Clear inherited legacy overrides in both service and stdio
                  # launches; private legacy values already resolve above.
                  "XHS_SEARCH_INTERVAL": "", "XHS_READ_INTERVAL": "",
                  "XHS_TOOL_TIMEOUT": str(self.timeout)+"s", "XHS_READ_CACHE_TTL": str(self.data.get("read_cache_seconds", 30))+"s",
                  "XHS_PUBLISH_INTERVAL": "60s", "XHS_ALLOW_RECONCILE": "1" if self.data.get("allow_reconcile") is True else "0",
                  "XHS_ALLOW_LOGIN_ACTIONS": "0"}
        values.update({"GOMEMLIMIT": str(self.resources["go_memory_limit_mb"])+"MiB",
                       "GOMAXPROCS": str(self.resources["go_max_procs"])})
        for key, setting, default, unit in (("XHS_SEARCH_MAX_PER_HOUR", "search_max_per_hour", 20, ""), ("XHS_SEARCH_CACHE_TTL", "search_cache_seconds", 900, "s"),
                ("XHS_DETAIL_CACHE_TTL", "detail_cache_seconds", 900, "s"),
                ("XHS_READ_MAX_PER_HOUR", "read_max_per_hour", 120, "")):
            values[key] = str(self.data.get(setting, default)) + unit
        return values

    def fingerprint(self):
        values = {**self.runtime_env(), "listen": self.data.get("listen", "127.0.0.1:18061")}
        return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def resolve(self, value):
        p = Path(value).expanduser()
        return (self.path.parent / p).resolve() if not p.is_absolute() else p.resolve()


def canonical_payload(tool, payload):
    clean = {k: v for k, v in payload.items() if k not in {"operation_id", "xsec_token", "run_id", "approval_ref"}}
    if tool == "search_feeds" and isinstance(clean.get("keyword"), str):
        clean["keyword"] = clean["keyword"].strip()
    # 图片改动参与去重，移动同一文件不会误认为另一篇帖。
    for field in ("images", "video"):
        if field not in clean:
            continue
        values = clean[field] if isinstance(clean[field], list) else [clean[field]]
        hashed = []
        for value in values:
            p = Path(value)
            if not value.startswith(("http://", "https://")) and p.is_file():
                digest = hashlib.sha256()
                with p.open("rb") as f:
                    for chunk in iter(lambda: f.read(1024*1024), b""):
                        digest.update(chunk)
                hashed.append("sha256:" + digest.hexdigest())
            else:
                hashed.append(value)
        clean[field] = hashed
    raw = json.dumps([tool, clean], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def request_parameters_digest(tool, payload):
    """Bind caller arguments without needing media after accepted cleanup."""
    clean = {k: v for k, v in payload.items() if k not in {"operation_id", "xsec_token", "run_id", "approval_ref"}}
    raw = json.dumps([tool, clean], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def local_request_assets(payload):
    for field in ("images", "video"):
        if field in payload:
            values = payload[field] if isinstance(payload[field], list) else [payload[field]]
            for index, value in enumerate(values):
                if not value.startswith(("http://", "https://")):
                    yield field, index, Path(value)


def asset_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_request_binding(tool, payload):
    # This small permanent record is separate from both existing fingerprints.
    # Indexed hashes let us still reject changed surviving files when another
    # page of the original publication has already been cleaned up.
    assets = {field + ":" + str(index): asset_digest(path)
              for field, index, path in local_request_assets(payload)}
    return json.dumps({"version": 1, "parameters": request_parameters_digest(tool, payload), "assets": assets}, separators=(",", ":"))


def verify_terminal_request(store, task, tool, payload):
    binding = task.get("request_binding")
    if binding:
        try:
            original = json.loads(binding)
            valid = (original["version"] == 1 and isinstance(original["parameters"], str)
                     and isinstance(original["assets"], dict))
        except (ValueError, KeyError, TypeError):
            valid = False
        if not valid:
            raise ValueError("ERR_RECEIPT_QUERY_REQUIRED: use get_operation_status with the original operation_id")
        if original["parameters"] != request_parameters_digest(tool, payload):
            raise ValueError("ERR_RECEIPT_MISMATCH: operation_id belongs to different content")
        for field, index, path in local_request_assets(payload):
            expected = original["assets"].get(field + ":" + str(index))
            if not expected:
                raise ValueError("ERR_RECEIPT_QUERY_REQUIRED: original asset binding unavailable; use get_operation_status")
            # Missing accepted assets are normal. Existing files remain fully
            # verifiable, and may not silently change under the same path.
            if path.is_file() and asset_digest(path) != expected:
                raise ValueError("ERR_RECEIPT_MISMATCH: original asset content changed")
    else:
        # Legacy rows have no original argument binding. Their unchanged
        # fingerprint is sufficient only while all caller media is available.
        if any(not path.is_file() for _, _, path in local_request_assets(payload)):
            raise ValueError("ERR_RECEIPT_QUERY_REQUIRED: original media unavailable; use get_operation_status with the original operation_id")
        if store.fingerprint(tool, payload) != task["fingerprint"]:
            raise ValueError("ERR_RECEIPT_MISMATCH: operation_id belongs to different content")


def go_mutation_fingerprint(tool, payload):
    """Mirror the unchanged Go typed mutationSignature for receipt binding."""
    assets = []
    if tool == "post_comment_to_feed":
        value = [payload.get("feed_id", ""), payload.get("content", "")]
    elif tool == "reply_comment_in_feed":
        value = [payload.get(k, "") for k in ("feed_id", "comment_id", "user_id", "content")]
    else:
        fields = ["title", "content", "images" if tool == "publish_content" else "video",
                  "tags", "schedule_at", "is_original", "visibility", "products", "activity"]
        if tool == "publish_content":
            fields += ["long_article_template", "topic_template"]
        value = {key: payload.get(key, "") for key in fields
                 if key in {"title", "content", "video"} or payload.get(key)}
        paths = payload.get("images", []) if tool == "publish_content" else [payload.get("video", "")]
        for path in paths:
            if path.startswith(("http://", "https://")):
                assets.append(path)
            else:
                h = hashlib.sha256()
                with Path(path).open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024*1024), b""):
                        h.update(chunk)
                assets.append(h.hexdigest())
    raw = json.dumps({"Payload": value, "Assets": assets}, ensure_ascii=False, separators=(",", ":"))
    for character, escaped in (("&", r"\u0026"), ("<", r"\u003c"), (">", r"\u003e"),
                               ("\u2028", r"\u2028"), ("\u2029", r"\u2029")):
        raw = raw.replace(character, escaped)
    return hashlib.sha256((tool + "\n" + raw).encode()).hexdigest()


DIRECT_RECEIPT = "direct-mcp-go-receipt"


def prepare_direct(store, request):
    """Authorize and bind one normal MCP call; never call MCP from this bridge."""
    tool, payload = request.get("tool_name"), request.get("payload")
    if tool not in WRITES or not isinstance(payload, dict):
        raise ValueError("ERR_DIRECT_TOOL: expected one supported content write")
    if not store.config.allow_write:
        raise ValueError("ERR_WRITE_NOT_ALLOWED: writes are disabled")
    payload = {k: v for k, v in payload.items() if k not in {"run_id", "approval_ref"}}
    operation_id = request.get("operation_id") or payload.pop("operation_id", None)
    payload.pop("operation_id", None)
    if operation_id and (not isinstance(operation_id, str) or len(operation_id.encode()) > 128 or operation_id.strip() != operation_id):
        raise ValueError("ERR_INVALID_OPERATION_ID")
    row = store.db.execute("SELECT * FROM tasks WHERE id=?", (operation_id,)).fetchone() if operation_id else None
    existing = dict(row) if row else None
    if existing and (existing["account"] != store.config.account or existing["tool"] != tool):
        raise ValueError("ERR_RECEIPT_MISMATCH: operation_id belongs to different content")
    if existing and request.get("run_id") and existing["run_id"] != request["run_id"]:
        raise ValueError("ERR_RECEIPT_TASK_BINDING: task belongs to its original run")

    def result(task, managed=False, receipt_only=False):
        value = {"task_id": task["id"], "operation_id": task["id"], "run_id": task["run_id"],
                 "approval_ref": task["approval_ref"], "managed_by_queue": managed, "status": task["status"]}
        if receipt_only:
            value["receipt_only"] = True
        else:
            value["payload"] = json.loads(task["payload"])
        return value

    if existing and existing["status"] in {"succeeded", "confirmed", "unknown"}:
        # An explicit original ID permits only its original receipt. Compare
        # caller arguments before lookup, without reopening cleaned media.
        verify_terminal_request(store, existing, tool, payload)
        return result(existing, existing["status"] in {"succeeded", "confirmed"} or existing["handling_evidence"] != DIRECT_RECEIPT, True)
    fingerprint = store.fingerprint(tool, payload)
    if existing is None:
        row = store.db.execute("SELECT * FROM tasks WHERE account=? AND fingerprint=?", (store.config.account, fingerprint)).fetchone()
        existing = dict(row) if row else None
    if existing and existing["fingerprint"] != fingerprint:
        raise ValueError("ERR_RECEIPT_MISMATCH: operation_id belongs to different content")
    if existing and request.get("run_id") and existing["run_id"] != request["run_id"]:
        raise ValueError("ERR_RECEIPT_TASK_BINDING: task belongs to its original run")
    if existing and existing["status"] in {"succeeded", "confirmed", "unknown"}:
        return result(existing, existing["status"] in {"succeeded", "confirmed"} or existing["handling_evidence"] != DIRECT_RECEIPT, True)
    if existing and existing["status"] == "running":
        if not existing["owner"] or not existing["lease_until"] or existing["lease_until"] <= store.now():
            raise ValueError("ERR_TASK_UNRESOLVED: expired execution requires original receipt reconciliation")
        if not existing["approval_ref"].strip():
            raise ValueError("ERR_APPROVAL_REQUIRED")
        if existing["run_id"]:
            store.continuous.authorize(existing["run_id"], tool, payload, existing["approval_ref"])
        # Legacy queued tasks without a run retain their original authorization;
        # new direct tasks always require a current reviewed run below.
        return result(existing, existing["handling_evidence"] != DIRECT_RECEIPT)
    if existing and (existing["status"] != "queued" or existing["auto_run"]):
        raise ValueError("ERR_TASK_UNRESOLVED: retain and handle the original task before another attempt")

    def waiting(task):
        if task["dependency"]:
            dependency = store.db.execute("SELECT status,next_at FROM tasks WHERE id=?", (task["dependency"],)).fetchone()
            if not dependency or dependency["status"] not in {"succeeded", "confirmed"}:
                status = dependency["status"] if dependency else "missing"
                return {**result(task), "deferred": True, "dependency_id": task["dependency"], "dependency_status": status,
                        "error_code": "ERR_DEPENDENCY_FAILED" if status in {"failed", "cancelled", "missing"} else "ERR_DEPENDENCY_PENDING"}
        due = store.task_due(task, store.request_deadlines())
        if due > store.now():
            return {**result(task), "deferred": True, "error_code": "ERR_RATE_LIMIT", "retry_at": due}
        return None

    if existing:
        deferred = waiting(existing)
        if deferred:
            return deferred

    continuous = store.continuous
    with continuous.lock():
        run_id = request.get("run_id") or (existing["run_id"] if existing else None)
        if not run_id:
            matches = []
            for row in store.db.execute("SELECT id FROM ops_runs WHERE state='active'"):
                run = continuous.run(row["id"])
                review = run["body"].get("review") or {}
                if review.get("fingerprints", {}).get(fingerprint) != tool:
                    continue
                try:
                    continuous.content.authorize(run, tool, payload)
                except (ValueError, OSError):
                    continue
                matches.append(run["id"])
            if len(matches) != 1:
                raise ValueError("ERR_CONTENT_MATCH_AMBIGUOUS: supply run_id for one current reviewed payload" if matches
                                 else "ERR_QUALITY_REVIEW_REQUIRED: no current reviewed payload matches")
            run_id = matches[0]
        approval_ref = request.get("approval_ref") or (existing["approval_ref"] if existing else "")
        if (store.db.execute("SELECT 1 FROM tasks WHERE status='unknown' AND id!=? LIMIT 1", (existing["id"] if existing else "",)).fetchone()
                and not request.get("approval_ref", "").strip()):
            raise ValueError("ERR_APPROVAL_REQUIRED: an independent direct action alongside unknown work needs explicit authorization")
        approved, approval_ref = continuous.authorize(run_id, tool, payload, approval_ref)
        value = store._enqueue(tool, approved, approval_ref, run_id=run_id, operation_id=operation_id,
                               request_binding=make_request_binding(tool, payload))
        with store.transaction():
            task = store.task(value["id"])
            if task["status"] != "queued" or task["auto_run"] or task["owner"]:
                raise ValueError("ERR_TASK_UNRESOLVED: the original task changed; inspect it without sending")
            deferred = waiting(task)
            if deferred:
                return deferred
            store.db.execute("UPDATE tasks SET status='running',attempts=attempts+1,owner=?,lease_until=?,handling_evidence=? WHERE id=?",
                             (uuid.uuid4().hex, store.now()+store.config.lease, DIRECT_RECEIPT, task["id"]))
            store.event(task["id"], "claimed_direct")
        return result(store.task(value["id"]))


def complete_direct(store, request):
    """Import the Go caller's local durable receipt, without an HTTP callback."""
    task = store.task(request["task_id"])
    if (task["tool"] not in WRITES or task["account"] != store.config.account or not task["run_id"]
            or not task["approval_ref"].strip() or task["auto_run"]
            or task["handling_evidence"] != DIRECT_RECEIPT):
        raise ValueError("ERR_RECEIPT_TASK_BINDING: only an original direct task can be completed")
    receipt = request.get("operation_status")

    def dated_receipt():
        stamp = receipt.get("updated_at")
        try:
            if not isinstance(stamp, str) or not re.search(r"(?:Z|[+-][0-9]{2}:[0-9]{2})$", stamp):
                raise ValueError()
            parse_timestamp(stamp)
        except ValueError:
            raise ValueError("ERR_RECEIPT_MISMATCH: dated Go receipt required") from None

    # resolve_operation records verified platform evidence, not a fabricated
    # tool result. Its dated, exact-content Go receipt can close an unknown.
    def verified_resolution():
        return (isinstance(receipt, dict) and receipt.get("status") == "confirmed"
                and receipt.get("result") is None
                and isinstance(receipt.get("evidence"), str) and bool(receipt["evidence"].strip()))

    if task["status"] in {"succeeded", "confirmed"}:
        # This task was already accounted for. Its approved media and full
        # payload may have been removed by normal retention; only read the
        # original receipt rather than reconstructing a mutation signature.
        if (not isinstance(receipt, dict) or receipt.get("id") != task["id"] or receipt.get("tool") != task["tool"]
                or receipt.get("status") != task["status"]
                or not (isinstance(receipt.get("result"), dict) or verified_resolution())):
            raise ValueError("ERR_RECEIPT_MISMATCH: terminal task and Go receipt disagree")
        prior = json.loads(task["result"]) if task["result"] else {}
        if prior.get("fingerprint") and receipt.get("fingerprint") != prior["fingerprint"]:
            raise ValueError("ERR_RECEIPT_MISMATCH: original durable fingerprint changed")
        dated_receipt()
        return {"task_id": task["id"], "operation_id": task["id"], "status": task["status"], "receipt_imported": True, "duplicate": True}
    payload = json.loads(task["payload"])
    if payload.get("operation_id") != task["id"] or store.fingerprint(task["tool"], payload) != task["fingerprint"]:
        raise ValueError("ERR_RECEIPT_MISMATCH: direct task content changed")
    code, next_at = request.get("error_code") or "", 0
    if receipt is not None:
        if (not isinstance(receipt, dict) or receipt.get("id") != task["id"] or receipt.get("tool") != task["tool"]
                or receipt.get("fingerprint") != go_mutation_fingerprint(task["tool"], payload)):
            raise ValueError("ERR_RECEIPT_MISMATCH: durable receipt does not identify this exact content")
        dated_receipt()
        # A local pacing deferral does not dispatch a mutation. On a resumed
        # task the journal can still hold its earlier pre-submit failure;
        # validate that receipt, but retain the current deferral deadline.
        if code == "ERR_RATE_LIMIT" and receipt.get("status") == "not_applied":
            receipt = None
    if receipt is not None:
        status = receipt.get("status")
        if status in {"succeeded", "confirmed"}:
            actual = receipt.get("result")
            valid = isinstance(actual, dict)
            if task["tool"] in {"post_comment_to_feed", "reply_comment_in_feed"}:
                valid = valid and actual.get("feed_id") == payload.get("feed_id") and actual.get("success") is True
                if task["tool"] == "reply_comment_in_feed":
                    valid = valid and actual.get("target_comment_id", "") == payload.get("comment_id", "") and actual.get("target_user_id", "") == payload.get("user_id", "")
            else:
                valid = valid and all(actual.get(k) == payload.get(k) for k in ("title", "content")) and actual.get("status") == "发布完成"
                valid = valid and (actual.get("images") == len(payload.get("images", [])) if task["tool"] == "publish_content" else actual.get("video") == payload.get("video"))
            if not valid and not verified_resolution():
                raise ValueError("ERR_RECEIPT_MISMATCH: successful receipt must contain the exact submitted result")
            code = None
        elif status == "not_applied":
            status, code = "blocked", code or "ERR_ACTION_NOT_SUBMITTED"
        else:
            status, code = "unknown", "ERR_OPERATION_UNKNOWN"
    elif code == "ERR_RATE_LIMIT":
        retry = request.get("retry_at") or 0
        next_at = parse_timestamp(retry) if isinstance(retry, str) else float(retry)
        next_at = max(store.now()+1, next_at)
        status = "queued"
    elif code in SAFE_NO_SUBMIT:
        status = "blocked"
    else:
        status, code = "unknown", code or "ERR_OPERATION_UNKNOWN"
    if task["status"] == "unknown":
        if receipt is None or status not in {"succeeded", "confirmed", "unknown"}:
            raise ValueError("ERR_TASK_UNRESOLVED: missing receipt cannot release an unknown action")
        with store.transaction():
            changed = store.db.execute("UPDATE tasks SET status=?,error_code=?,result=?,completed_at=? WHERE id=? AND status='unknown'",
                                       (status, code, json.dumps(receipt, ensure_ascii=False), store.now() if status in {"succeeded", "confirmed"} else 0, task["id"])).rowcount
            if changed != 1:
                raise ValueError("ERR_RECEIPT_TASK_CHANGED: inspect the original receipt and current task")
            store.event(task["id"], "reconciled_direct", code)
    elif task["status"] == "running" and task["owner"]:
        store.finish(task, status, code, result=receipt or {"error_code": code}, next_at=next_at,
                     server_id=task["id"], deferred=code == "ERR_RATE_LIMIT")
    else:
        raise ValueError("ERR_TASK_UNRESOLVED: completion requires the original running task")
    return {"task_id": task["id"], "operation_id": task["id"], "status": status,
            "next_at": next_at, "receipt_imported": receipt is not None}


class Store:
    def __init__(self, config, now=time.time):
        self.config, self.now = config, now
        config.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        config.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = config.state_dir / "tasks.sqlite3"
        if config.state_id and (not self.path.exists() or self.path.stat().st_size == 0): raise ValueError("ERR_STATE_LOST: 已绑定队列不存在，必须恢复私有备份，禁止创建空库")
        self.db = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
        os.chmod(self.path, 0o600)
        self.db.row_factory = sqlite3.Row
        if config.state_id:
            try:
                prior = self.db.execute("SELECT value FROM meta WHERE key='state_id'").fetchone()
            except sqlite3.Error:
                self.db.close(); raise ValueError("ERR_STATE_LOST: 已绑定队列结构不存在，禁止重建")
            if not prior or prior[0] != config.state_id:
                self.db.close(); raise ValueError("ERR_STATE_INCOMPATIBLE: 队列状态身份不匹配")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA cache_size=-2048")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tasks (
          id TEXT PRIMARY KEY, account TEXT NOT NULL, tool TEXT NOT NULL, fingerprint TEXT NOT NULL,
          payload TEXT NOT NULL, approval_ref TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0, lease_until REAL,
          owner TEXT, result TEXT, error_code TEXT, server_id TEXT, dependency TEXT, handling_evidence TEXT,
          UNIQUE(account,fingerprint));
        CREATE TABLE IF NOT EXISTS events (
          seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, task_id TEXT, kind TEXT NOT NULL, code TEXT);
        """)
        with self.transaction():
            columns = {r[1] for r in self.db.execute("PRAGMA table_info(tasks)")}
            for name, definition in (("auto_run", "INTEGER NOT NULL DEFAULT 0"), ("recovery_count", "INTEGER NOT NULL DEFAULT 0"), ("probe_count", "INTEGER NOT NULL DEFAULT 0"), ("blocked_at", "REAL NOT NULL DEFAULT 0"), ("reconcile_after", "REAL NOT NULL DEFAULT 0"), ("run_id", "TEXT"), ("completed_at", "REAL NOT NULL DEFAULT 0"), ("request_binding", "TEXT")):
                if name not in columns:
                    self.db.execute("ALTER TABLE tasks ADD COLUMN " + name + " " + definition)
            if "completed_at" not in columns:
                # Start the retention clock conservatively for legacy rows. Never
                # infer that pending/unknown work is old enough to discard.
                self.db.execute("UPDATE tasks SET completed_at=? WHERE status IN ('succeeded','confirmed','failed','cancelled')", (self.now(),))
            self.db.execute("CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status)")
            self.db.execute("CREATE INDEX IF NOT EXISTS tasks_dependency ON tasks(dependency)")
            self.db.execute("CREATE INDEX IF NOT EXISTS tasks_run ON tasks(run_id)")
            self.db.execute("CREATE INDEX IF NOT EXISTS tasks_completed ON tasks(completed_at) WHERE completed_at>0")
        version = self.db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        if version and version[0] != "1":
            raise ValueError("state schema incompatible; preserve database and restore matching version")
        self.db.execute("INSERT OR IGNORE INTO meta VALUES ('schema','1')")
        account = self.db.execute("SELECT value FROM meta WHERE key='account'").fetchone()
        if account and account[0] != config.account:
            raise ValueError("state directory belongs to another account")
        self.db.execute("INSERT OR IGNORE INTO meta VALUES ('account',?)", (config.account,))
        self._continuous = None

    @property
    def continuous(self):
        # Reading a task, connecting MCP or checking core state does not need
        # content/batch tables. Load those only when their capability is used.
        if self._continuous is None:
            from continuous_ops import ContinuousOps
            self._continuous = ContinuousOps(self)
        return self._continuous

    def has_table(self, name):
        return self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

    fingerprint = staticmethod(canonical_payload)

    @contextlib.contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def event(self, task_id, kind, code=None):
        self.db.execute("INSERT INTO events(at,task_id,kind,code) VALUES (?,?,?,?)", (self.now(), task_id, kind, code))
        # Keep a bounded diagnostic tail; submission records remain in the separate journal.
        self.db.execute("DELETE FROM events WHERE seq < (SELECT max(seq)-10000 FROM events)")

    def resources(self, required_bytes=0):
        """Cheap admission check, no recursive scans or browser requests."""
        limits = self.config.resources
        state_bytes = 0
        for path in self.config.state_dir.iterdir():
            try: info = path.lstat()
            except FileNotFoundError: continue  # WAL/log rotation can remove a file concurrently.
            if stat.S_ISREG(info.st_mode): state_bytes += info.st_size
        # State and output may be on different filesystems; protect both.
        disks = [shutil.disk_usage(p).free for p in (self.config.state_dir, self.config.output_dir)]
        inodes = [v.f_favail for v in (os.statvfs(self.config.state_dir), os.statvfs(self.config.output_dir)) if v.f_files]
        reason = None
        if state_bytes >= limits["state_disk_limit_mb"] * 1024**2:
            reason = "state_budget"
        elif min(disks) - required_bytes < limits["min_free_disk_mb"] * 1024**2:
            reason = "free_disk_reserve"
        elif inodes and min(inodes) < limits["min_free_inodes"]:
            reason = "free_inode_reserve"
        return {"admission_allowed": reason is None, "pressure": reason, "state_bytes": state_bytes,
                "free_disk_bytes": min(disks), "free_inodes": min(inodes) if inodes else None,
                "limits": limits, "retry_after_seconds": 300 if reason else None}

    def require_resources(self, required_bytes=0):
        pressure = self.resources(required_bytes)
        if not pressure["admission_allowed"]:
            raise ValueError("ERR_RESOURCE_BUDGET: " + pressure["pressure"] + "; retain receipts/evidence; retry after space is available")

    def maintain_history(self):
        """Discard expired large details, never identities, dedup or live evidence."""
        retention = self.config.data.get("retention", {})
        reads_before = self.now() - retention.get("read_keep_hours", 24)*3600
        writes_before = self.now() - retention.get("write_detail_keep_days", 7)*86400
        inactive = ("(run_id IS NULL OR NOT EXISTS (SELECT 1 FROM ops_runs r WHERE r.id=tasks.run_id))"
                    if self.has_table("ops_runs") else "run_id IS NULL")
        with self.transaction():
            # Old runtimes do not stamp this new column when a rollback finishes
            # a task. Start its clock on return; do not keep those blobs forever.
            self.db.execute("UPDATE tasks SET completed_at=? WHERE completed_at=0 AND status IN ('succeeded','confirmed','failed','cancelled')", (self.now(),))
            deleted = self.db.execute("DELETE FROM tasks WHERE completed_at>0 AND completed_at<? AND status IN ('succeeded','confirmed','failed','cancelled') AND tool NOT IN ('post_comment_to_feed','reply_comment_in_feed','publish_content','publish_with_video') AND " + inactive + " AND NOT EXISTS (SELECT 1 FROM tasks d WHERE d.dependency=tasks.id)", (reads_before,)).rowcount
            # Referenced reads retain a small success/dependency tombstone. Write
            # fingerprints, request bindings and IDs remain permanent; do not
            # reset the journal or remove the hashes needed after media cleanup.
            compacted = self.db.execute("UPDATE tasks SET payload='{}',result='{\"archived\":true}' WHERE completed_at>0 AND completed_at<? AND status IN ('succeeded','confirmed','failed','cancelled') AND " + inactive + " AND tool NOT IN ('post_comment_to_feed','reply_comment_in_feed','publish_content','publish_with_video') AND (payload!='{}' OR result IS NOT NULL AND result!='{\"archived\":true}')", (reads_before,)).rowcount
            for row in self.db.execute("SELECT id,payload FROM tasks WHERE completed_at>0 AND completed_at<? AND status IN ('succeeded','confirmed','failed','cancelled') AND " + inactive + " AND tool IN ('post_comment_to_feed','reply_comment_in_feed','publish_content','publish_with_video') AND (result IS NULL OR result NOT LIKE '%\"archived\":true%')", (writes_before,)):
                payload = json.loads(row["payload"])
                small = {k: payload[k] for k in ("feed_id", "user_id", "comment_id", "target_user_id") if k in payload}
                self.db.execute("UPDATE tasks SET payload=?,result='{\"archived\":true}' WHERE id=?", (json.dumps(small), row["id"]))
                compacted += 1
        # Reuse database pages; never run a full VACUUM on the live queue.
        self.db.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchall()
        return {"expired_read_tasks_removed": deleted, "details_compacted": compacted}

    def enqueue(self, tool, payload, approval_ref="", not_before=0, dependency=None, run_id=None):
        if run_id:
            with self.continuous.lock():
                original = dict(payload)
                payload, approval_ref = self.continuous.authorize(run_id, tool, dict(payload), approval_ref)
                return self._enqueue(tool, payload, approval_ref, not_before, dependency, run_id,
                                     request_binding=make_request_binding(tool, original) if tool in WRITES else None)
        return self._enqueue(tool, payload, approval_ref, not_before, dependency)

    def _enqueue(self, tool, payload, approval_ref="", not_before=0, dependency=None, run_id=None, operation_id=None, request_binding=None):
        if tool not in READS | WRITES or not isinstance(payload, dict):
            raise ValueError("unsupported tool/payload")
        payload = dict(payload)
        if tool == "publish_content":
            if len(payload.get("images", [])) > 5: raise ValueError("当前图文运营每帖最多5页；不静默截断")
            if not payload.get("images") and not payload.get("long_article_template"):
                raise ValueError("publication requires images or an explicitly selected article template")
        if tool in {"publish_content", "publish_with_video"}:
            for key in ("title", "content"):
                if not isinstance(payload.get(key), str) or not payload[key].strip():
                    raise ValueError(key + " required")
            for field in ("images", "video"):
                values = payload.get(field, [])
                if isinstance(values, str):
                    values = [values]
                for value in values:
                    if value.startswith(("http://", "https://")):
                        continue
                    p = Path(value)
                    if not p.is_absolute() or not p.is_file():
                        raise ValueError("asset path must be absolute, readable and on the MCP execution host")
        fingerprint = canonical_payload(tool, payload)
        if tool in WRITES and request_binding is None:
            request_binding = make_request_binding(tool, payload)
        if run_id and tool in READS:
            fingerprint += ":run:" + run_id
        with self.transaction():
            if tool in READS:
                query = "SELECT * FROM tasks WHERE account=? AND (fingerprint=? OR fingerprint GLOB ?)"
                parameters = [self.config.account, fingerprint, fingerprint + ":read:*"]
                if run_id:
                    query += " AND run_id=?"
                    parameters.append(run_id)
                old = self.db.execute(query + " ORDER BY rowid DESC LIMIT 1", parameters).fetchone()
                if old and old["status"] == "succeeded":
                    completed = self.db.execute("SELECT max(at) FROM events WHERE task_id=? AND kind='succeeded'", (old["id"],)).fetchone()[0]
                    ttl = self.config.data.get("search_cache_seconds", 900) if tool == "search_feeds" else self.config.data.get("detail_cache_seconds", 900) if tool == "get_feed_detail" else self.config.data.get("read_cache_seconds", 30) if tool in SITE_READS else 0
                    if (payload.get("fresh") is True and not run_id) or not ttl or completed is None or self.now() - completed >= ttl:
                        # Keep the old snapshot/task intact. A later research
                        # request gets a new read record, still paced by MCP.
                        fingerprint += ":read:" + uuid.uuid4().hex
                        old = None
            else:
                old = self.db.execute("SELECT * FROM tasks WHERE account=? AND fingerprint=?", (self.config.account, fingerprint)).fetchone()
            if old:
                return {"id": old["id"], "status": old["status"], "duplicate": True}
            if dependency and not self.db.execute("SELECT 1 FROM tasks WHERE id=?", (dependency,)).fetchone():
                raise ValueError("dependency not found")
            self.require_resources()
            task_id = operation_id or uuid.uuid4().hex
            if self.db.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone():
                raise ValueError("ERR_IDEMPOTENCY_CONFLICT: original operation_id belongs to different content")
            if tool in WRITES:
                payload["operation_id"] = task_id
            self.db.execute("INSERT INTO tasks(id,account,tool,fingerprint,payload,approval_ref,status,next_at,dependency,run_id,request_binding) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (task_id, self.config.account, tool, fingerprint, json.dumps(payload, ensure_ascii=False), approval_ref, "queued", not_before, dependency, run_id, request_binding))
            if run_id:
                self.continuous.bind_slot(run_id, tool, payload, task_id)
            self.event(task_id, "enqueued")
        return {"id": task_id, "status": "queued", "duplicate": False}

    def verify_state(self):
        verify_runtime_binding(self.config)

    def request_deadlines(self):
        """Read the MCP's durable pacing ledger locally; this never probes the site."""
        path = self.config.state_dir / "runtime-state.json"
        if not path.is_file():
            return {}
        data = json.loads(path.read_text())
        if data.get("state_id") != self.config.state_id or data.get("account") != self.config.account:
            raise ValueError("ERR_STATE_INCOMPATIBLE: MCP 状态和队列账号不匹配")
        deadlines = {key: parse_timestamp(data[key])
                     for key in ("next_write_at", "next_publish_at", "next_search_at", "next_read_at") if data.get(key)}
        # Read the old queue deadline only until it expires. New deadlines are
        # drawn and persisted exclusively by the browser executor.
        legacy = self.db.execute("SELECT value FROM meta WHERE key='next_write_at'").fetchone()
        if legacy and float(legacy[0]) > self.now():
            deadlines["next_write_at"] = max(deadlines.get("next_write_at", 0), float(legacy[0]))
        elif legacy:
            self.db.execute("DELETE FROM meta WHERE key='next_write_at'")
        return deadlines

    def task_due(self, task, deadlines, next_write=0):
        due = task["next_at"]
        if task["tool"] in SITE_READS:
            due = max(due, deadlines.get("next_read_at", 0))
        if task["tool"] == "search_feeds":
            due = max(due, deadlines.get("next_search_at", 0))
        if task["tool"] in WRITES:
            due = max(due, next_write, deadlines.get("next_write_at", 0))
        if task["tool"] in {"publish_content", "publish_with_video"}:
            due = max(due, deadlines.get("next_publish_at", 0))
        return due

    def settle_dependencies(self):
        """Inside a transaction: skip work whose prerequisite definitively ended."""
        rows = self.db.execute("""WITH RECURSIVE skipped(id) AS (
          SELECT t.id FROM tasks t JOIN tasks d ON d.id=t.dependency
            WHERE t.status='queued' AND d.status IN ('failed','cancelled')
          UNION SELECT t.id FROM tasks t JOIN skipped d ON d.id=t.dependency WHERE t.status='queued')
          SELECT id FROM skipped""").fetchall()
        for row in rows:
            self.db.execute("UPDATE tasks SET status='cancelled',error_code='ERR_DEPENDENCY_FAILED',completed_at=? WHERE id=? AND status='queued'", (self.now(), row[0]))
            if self.has_table("ops_calendar"):
                self.db.execute("DELETE FROM ops_calendar WHERE task_id=?", (row[0],))
            self.event(row[0], "cancelled", "ERR_DEPENDENCY_FAILED")

    def claim(self, automatic=False):
        now = self.now()
        if self.config.allow_write:
            self.verify_state()
        if self.db.execute("SELECT 1 FROM tasks WHERE run_id IS NOT NULL LIMIT 1").fetchone():
            self.continuous  # Initialize optional tables before the claim transaction.
        deadlines = self.request_deadlines()
        with self.transaction():
            self.settle_dependencies()
            expired = self.db.execute("SELECT * FROM tasks WHERE status='running' AND lease_until<=?", (now,)).fetchall()
            for task in expired:
                status = "unknown" if task["tool"] in WRITES else "failed" if task["attempts"] >= self.config.max_attempts else "queued"
                self.db.execute("UPDATE tasks SET status=?,error_code='ERR_WORKER_INTERRUPTED',lease_until=NULL,owner=NULL WHERE id=?", (status, task["id"]))
                self.event(task["id"], "alert", "ERR_WORKER_INTERRUPTED")
            if self.db.execute("SELECT 1 FROM tasks WHERE status='running'").fetchone():
                return None
            if not self.resources()["admission_allowed"]:
                return None  # No claim, attempt, browser request or loss of evidence.
            unresolved = self.db.execute("SELECT 1 FROM tasks WHERE status='unknown'").fetchone()
            account_blocked = self.db.execute("SELECT value FROM meta WHERE key='account_blocked'").fetchone()
            candidates = self.db.execute("SELECT * FROM tasks WHERE status='queued' AND next_at<=? ORDER BY rowid", (now,))
            for task in candidates:
                if automatic and not task["auto_run"]:
                    continue
                if account_blocked and task["tool"] not in {"get_operation_status", "get_runtime_status"}:
                    continue
                if task["dependency"]:
                    dep = self.db.execute("SELECT status FROM tasks WHERE id=?", (task["dependency"],)).fetchone()
                    if dep[0] not in {"succeeded", "confirmed"}:
                        continue
                due = self.task_due(task, deadlines)
                if now < due:
                    # Persist the real deadline instead of claiming/retrying the
                    # same task every few minutes or spending a failure attempt.
                    self.db.execute("UPDATE tasks SET next_at=? WHERE id=?", (due, task["id"]))
                    continue
                if task["tool"] in WRITES:
                    if unresolved:
                        continue
                    if not self.config.allow_write or not task["approval_ref"]:
                        code = "ERR_WRITE_NOT_ALLOWED" if not self.config.allow_write else "ERR_APPROVAL_REQUIRED"
                        self.db.execute("UPDATE tasks SET status='blocked',error_code=? WHERE id=?", (code, task["id"]))
                        self.event(task["id"], "alert", code)
                        continue
                    if task["run_id"] and canonical_payload(task["tool"], json.loads(task["payload"])) != task["fingerprint"]:
                        self.db.execute("UPDATE tasks SET status='blocked',error_code='ERR_ASSET_CHANGED' WHERE id=?", (task["id"],))
                        self.event(task["id"], "alert", "ERR_ASSET_CHANGED")
                        continue
                    if task["run_id"]:
                        try:
                            self.continuous.content.authorize(self.continuous.run(task["run_id"]),
                                                              task["tool"], json.loads(task["payload"]))
                        except (ValueError, OSError) as failure:
                            code = str(failure).split(":", 1)[0]
                            if not code.startswith("ERR_"):
                                code = "ERR_CONTENT_REVIEW_STALE"
                            self.db.execute("UPDATE tasks SET status='blocked',error_code=? WHERE id=?", (code, task["id"]))
                            self.event(task["id"], "alert", code)
                            continue
                    # MCP draws the shared deadline at the real site action.
                owner = uuid.uuid4().hex
                self.db.execute("UPDATE tasks SET status='running',attempts=attempts+1,owner=?,lease_until=? WHERE id=?", (owner, now + self.config.lease, task["id"]))
                self.event(task["id"], "claimed")
                claimed = dict(self.db.execute("SELECT * FROM tasks WHERE id=?", (task["id"],)).fetchone())
                return claimed
        return None

    def finish(self, task, status, code=None, result=None, next_at=0, server_id=None, deferred=False):
        with self.transaction():
            changed = self.db.execute("UPDATE tasks SET status=?,error_code=?,result=?,next_at=?,server_id=?,owner=NULL,lease_until=NULL WHERE id=? AND owner=? AND status='running'", (status, code, json.dumps(result, ensure_ascii=False) if result is not None else None, next_at, server_id, task["id"], task["owner"])).rowcount
            if changed != 1:
                raise ValueError("lease lost; never overwrite another worker's recovery decision")
            self.db.execute("UPDATE tasks SET completed_at=? WHERE id=?", (self.now() if status in {"succeeded", "confirmed", "failed", "cancelled"} else 0, task["id"]))
            if deferred:
                # A local pacing decision made no site request. It is normal
                # queueing, not a failed execution or another write interval.
                self.db.execute("UPDATE tasks SET attempts=max(0,attempts-1) WHERE id=?", (task["id"],))
            if status == "blocked" and code in BLOCKERS:
                self.db.execute("INSERT OR REPLACE INTO meta VALUES ('account_blocked',?)", (code,))
            if status == "blocked":
                self.db.execute("UPDATE tasks SET blocked_at=? WHERE id=?", (self.now(), task["id"]))
            self.event(task["id"], "deferred" if deferred else "alert" if status in {"blocked", "unknown", "failed"} else status, code)

    def status(self, automatic=False):
        counts = {r[0]: r[1] for r in self.db.execute("SELECT status,count(*) FROM tasks GROUP BY status")}
        account_blocked = self.db.execute("SELECT value FROM meta WHERE key='account_blocked'").fetchone() is not None
        unresolved = self.db.execute("SELECT 1 FROM tasks WHERE status='unknown'").fetchone() is not None
        deadlines = self.request_deadlines()
        times, runner_times = [], []
        for task in self.db.execute("SELECT t.tool,t.next_at,t.auto_run FROM tasks t LEFT JOIN tasks d ON t.dependency=d.id WHERE t.status='queued' AND (t.dependency IS NULL OR d.status IN ('succeeded','confirmed'))"):
            if automatic and not task["auto_run"]:
                continue
            if account_blocked and task["tool"] not in {"get_operation_status", "get_runtime_status"}:
                continue
            if task["tool"] in WRITES:
                if unresolved:
                    continue
            due = self.task_due(task, deadlines)
            times.append(due)
            if task["auto_run"]:
                runner_times.append(due)
        next_at = min(times) if times else None
        enabled = self.db.execute("SELECT value FROM meta WHERE key='runner_enabled'").fetchone()
        runner_enabled = bool(enabled and enabled[0] == "1")
        pressure = self.resources()
        last = self.db.execute("SELECT value FROM meta WHERE key='last_ops_maintenance'").fetchone()
        wake_times = [max(self.now()+1, float(last[0])+3600) if last else self.now()+1]
        if runner_enabled:
            if runner_times:
                wake_times.append(max(min(runner_times), self.now()+300) if not pressure["admission_allowed"] else min(runner_times))
            lease = self.db.execute("SELECT min(lease_until) FROM tasks WHERE status='running'").fetchone()[0]
            if lease is not None:
                wake_times.append(lease)
            recoverable = counts.get("blocked") and self.db.execute("SELECT 1 FROM tasks WHERE status='blocked' AND error_code IN ('ERR_BROWSER_UNAVAILABLE','ERR_BROWSER_NOT_CONNECTED','ERR_MCP_NOT_DISPATCHED','ERR_NOT_LOGGED_IN','ERR_LOGIN_STATE_UNKNOWN','ERR_CAPTCHA') AND recovery_count<? LIMIT 1", (self.config.recovery_limit,)).fetchone()
            if unresolved or recoverable:
                wake_times.append(self.now()+(60 if pressure["admission_allowed"] else 300))
        outcomes = {r[0]: r[1] for r in self.db.execute("SELECT kind,count(*) FROM events GROUP BY kind")}
        pending = []
        for row in self.db.execute("SELECT id,tool,status,next_at FROM tasks WHERE status IN ('queued','running','unknown','blocked') ORDER BY rowid LIMIT 50"):
            item = dict(row)
            if item["status"] == "queued":
                item["next_at"] = self.task_due(row, deadlines)
            pending.append(item)
        return {"version": VERSION, "account": self.config.account, "output_dir": str(self.config.output_dir), "counts": counts, "next_at": next_at,
                "runner_next_at": min(runner_times) if runner_times else None, "background_next_at": min(wake_times),
                "resources": pressure, "pending_tasks": pending, "runner_enabled": runner_enabled, "account_blocked": account_blocked,
                "continuous_operation": self.continuous.status() if self.has_table("ops_policy") else {"mode": "not_configured", "active_run": None, "acceptance_run": None}, "event_counts_last_10000": outcomes,
                "alerts": [dict(r) for r in self.db.execute("SELECT seq,at,task_id,code FROM events WHERE kind='alert' ORDER BY seq DESC LIMIT 20")]}

    def task_view(self, task_id, include_result=False):
        task = self.task(task_id)
        value = {k: task[k] for k in ("id", "tool", "status", "attempts", "next_at", "lease_until", "error_code", "server_id", "recovery_count")}
        if task["status"] == "queued":
            value["next_at"] = self.task_due(task, self.request_deadlines())
        if include_result and task["result"]:
            value["result"] = json.loads(task["result"])
        activity = self.config.state_dir / "activity.json"
        if activity.is_file():
            try:
                data = json.loads(activity.read_text())
                matches = [r for r in data.get("active", []) + data.get("recent", []) if r.get("task_id") == task_id]
                if matches:
                    value["progress"] = max(matches, key=lambda r: r.get("updated_at", ""))
                    progress = value["progress"]
                    if progress.get("status") == "running":
                        if task["status"] != "running":
                            progress["status"] = "interrupted_record"
                        else:
                            start = dt.datetime.fromisoformat(progress["started_at"].replace("Z", "+00:00")).timestamp()
                            progress["seconds"] = max(0, self.now() - start)
            except (OSError, ValueError):
                value["progress_unavailable"] = True
        return value

    def task(self, task_id):
        row = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise ValueError("task not found")
        return dict(row)

    def resume(self, task_id, evidence, approval_ref=""):
        with self.transaction():
            task = self.task(task_id)
            if task["status"] != "blocked" or not evidence.strip():
                raise ValueError("only blocked tasks may resume with actual handling evidence; unknown requires reconcile")
            self.db.execute("UPDATE tasks SET status='queued',next_at=?,error_code=NULL,approval_ref=?,handling_evidence=? WHERE id=?", (self.now(), approval_ref or task["approval_ref"], evidence, task_id))
            self.db.execute("DELETE FROM meta WHERE key='account_blocked'")
            # evidence 存任务私有记录；告警日志不写登录信息或凭据。
            self.event(task_id, "resumed")


class ToolFailure(Exception):
    def __init__(self, code, server_id=None, retry_at=0):
        super().__init__(code)
        self.code, self.server_id, self.retry_at = code, server_id, retry_at


class MCPTransport:
    def __init__(self, config, timeout=None):
        self.config, self.session = config, None
        self.timeout = min(config.timeout, timeout) if timeout is not None else config.timeout
        self.deadline = None
        self.protocol = "2025-11-25"

    def request(self, method, params=None, notification=False):
        body = {"jsonrpc": "2.0", "method": method}
        if not notification:
            body["id"] = uuid.uuid4().hex
        if params is not None:
            body["params"] = params
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": self.protocol}
        if self.config.token:
            headers["Authorization"] = "Bearer " + self.config.token
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(self.config.url, data=json.dumps(body).encode(), headers=headers)
        try:
            remaining = self.timeout if self.deadline is None else self.deadline - time.monotonic()
            if remaining <= 0: raise ToolFailure("ERR_TIMEOUT")
            with urllib.request.urlopen(req, timeout=remaining) as response:
                self.session = response.headers.get("Mcp-Session-Id", self.session)
                raw = response.read(8 * 1024 * 1024 + 1)
                if notification and not raw:
                    return {}
                if len(raw) > 8 * 1024 * 1024:
                    raise ToolFailure("ERR_RESPONSE_TOO_LARGE")
                if "text/event-stream" in response.headers.get("Content-Type", ""):
                    messages = [json.loads(line[5:].strip()) for line in raw.decode().splitlines() if line.startswith("data:") and line[5:].strip()]
                    value = next((m for m in messages if m.get("id") == body.get("id")), None)
                    if value is None:
                        raise ToolFailure("ERR_PROTOCOL")
                else:
                    value = json.loads(raw)
                if not notification and (value.get("id") != body["id"] or value.get("jsonrpc") != "2.0"):
                    raise ToolFailure("ERR_PROTOCOL")
                if "error" in value:
                    raise ToolFailure("ERR_PROTOCOL")
                return value.get("result", {})
        except urllib.error.HTTPError as e:
            raise ToolFailure({401: "ERR_AUTH", 403: "ERR_PERMISSION", 429: "ERR_QUOTA"}.get(e.code, "ERR_HTTP_" + str(e.code))) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise ToolFailure("ERR_NETWORK_OR_PROTOCOL") from None

    def connect(self):
        result = self.request("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "xhs-operations", "version": VERSION}})
        if "protocolVersion" not in result:
            raise ToolFailure("ERR_PROTOCOL")
        self.protocol = result["protocolVersion"]
        self.request("notifications/initialized", notification=True)

    def call(self, tool, arguments):
        import threading
        main_thread = threading.current_thread() is threading.main_thread()
        previous = None
        timer = (0, 0)
        started = time.monotonic()
        if main_thread:
            def timeout_handler(*_): raise ToolFailure("ERR_TIMEOUT")
            previous = signal.signal(signal.SIGALRM, timeout_handler)
            timer = signal.setitimer(signal.ITIMER_REAL, self.timeout)
        try:
            return self._call(tool, arguments)
        finally:
            if main_thread:
                signal.setitimer(signal.ITIMER_REAL, max(0.000001, timer[0] - (time.monotonic()-started)) if timer[0] else 0, timer[1])
                signal.signal(signal.SIGALRM, previous)

    def _call(self, tool, arguments):
        self.deadline = time.monotonic() + self.timeout
        self.connect()
        result = self.request("tools/call", {"name": tool, "arguments": arguments, "_meta": {"xhs/task_id": getattr(self, "task_id", "")}})
        text = "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        if result.get("isError"):
            codes = re.findall(r"ERR_[A-Z_]+", text)
            code = codes[0] if codes else "ERR_WRITE_NOT_ALLOWED" if "写操作未开启" in text else "ERR_TOOL_FAILED"
            if code in {"ERR_COMMENT_NOT_SUBMITTED", "ERR_ACTION_NOT_SUBMITTED"}:
                code = "ERR_BROWSER_NOT_CONNECTED" if "ERR_BROWSER_UNAVAILABLE" in codes else next((item for item in codes if item in BLOCKERS or item == "ERR_RATE_LIMITED"), "ERR_COMMENT_TEMPORARY_PAGE_FAILURE" if "ERR_COMMENT_TEMPORARY_PAGE_FAILURE" in codes or "ERR_PAGE_READY_TIMEOUT" in codes else code)
            op = re.search(r"operation_id=([a-zA-Z0-9_-]+)", text)
            retry = re.search(r"retry_at=([0-9T:.+Z-]+)", text)
            retry_at = 0
            if retry:
                try:
                    retry_at = parse_timestamp(retry[1])
                except ValueError:
                    pass
            raise ToolFailure(code, op[1] if op else None, retry_at)
        if result.get("structuredContent") is not None:
            return result["structuredContent"]
        if not text.strip():
            raise ToolFailure("ERR_RESULT_EMPTY")
        try:
            return json.loads(text)
        except ValueError:
            return {"text": text}


def tick(store, transport, automatic=False):
    with (store.config.state_dir / "execution.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "busy"}
        return _tick(store, transport, automatic)


def _tick(store, transport, automatic=False):
    task = store.claim(automatic=automatic)
    if task is None:
        return {"status": "idle", **store.status()}
    try:
        transport.task_id = task["id"]
        result = transport.call(task["tool"], json.loads(task["payload"]))
        store.finish(task, "succeeded", result=result)
    except ToolFailure as e:
        if task["tool"] in WRITES and e.code not in SAFE_NO_SUBMIT:
            status, next_at = "unknown", 0
        elif e.code == "ERR_RATE_LIMITED":
            # Platform throttling is held for explicit recovery, never retried by a timer.
            status, next_at = "blocked", 0
        elif e.code == "ERR_RATE_LIMIT":
            # Not dispatched: retain authorization and resume this exact task at
            # its actual slot, without consuming retries or adding a fixed wait.
            status, next_at = "queued", max(store.now() + 1, e.retry_at)
        elif (e.code in TRANSIENT or (e.code == "ERR_BROWSER_UNAVAILABLE" and task["tool"] in READS)) and task["attempts"] < store.config.max_attempts:
            # 服务已明确记录未点发送；等下一时刻再尝试，仍保留同一任务/授权/参数。
            status = "queued"
            next_at = store.now() + min(120, 15 * 2 ** task["attempts"])
            if task["tool"] in WRITES:
                next_at = max(next_at, store.request_deadlines().get("next_write_at", 0))
        elif e.code in BLOCKERS or e.code in SAFE_NO_SUBMIT:
            status, next_at = "blocked", 0
        elif task["attempts"] < store.config.max_attempts:
            status, next_at = "queued", store.now() + min(60, 2 ** task["attempts"] * 5)
        else:
            status, next_at = "failed", 0
        store.finish(task, status, e.code, next_at=next_at, server_id=e.server_id, deferred=e.code == "ERR_RATE_LIMIT")
    except Exception:
        # 写异常不可假设没提交；持久化失败则租约到期后恢复。
        store.finish(task, "unknown" if task["tool"] in WRITES else "failed", "ERR_EXECUTOR")
    view = store.task_view(task["id"])
    return {"id": task["id"], "status": view["status"], "next_at": view["next_at"]}


def reconcile(store, transport, task_id):
    task = store.task(task_id)
    if task["handling_evidence"] == DIRECT_RECEIPT:
        try:
            receipt = transport.call("get_operation_status", {"operation_id": task_id})
        except ToolFailure:
            if (task["status"] == "running" and task["lease_until"] is not None
                    and task["lease_until"] <= store.now()):
                store.finish(task, "unknown", "ERR_WORKER_INTERRUPTED", server_id=task_id)
            raise  # A missing receipt never proves that the site was untouched.
        value = complete_direct(store, {"task_id": task_id, "operation_status": receipt})
        return {"id": task_id, **value}
    imported_marker = "direct-comment-go-receipt"
    repeated_import = (task["status"] in {"succeeded", "confirmed"}
                       and task["handling_evidence"] == imported_marker)
    if ((task["status"] == "queued" and task["attempts"] == 0 and not task["auto_run"])
            or repeated_import) and task["tool"] == "post_comment_to_feed":
        # An independently authorized direct call can share this real task ID.
        # Import only its durable Go receipt; this path never dispatches a write.
        if (task["owner"] not in {None, ""} or task["lease_until"] is not None or not task["approval_ref"].strip()
                or (repeated_import and (task["attempts"] != 1 or task["auto_run"] != 0))
                or task["account"] != store.config.account or not task["run_id"]
                or not store.db.execute("SELECT 1 FROM ops_runs WHERE id=?", (task["run_id"],)).fetchone()):
            raise ValueError("ERR_RECEIPT_TASK_BINDING: unowned manual task, approval and original run required")
        payload = json.loads(task["payload"])
        if (payload.get("operation_id") != task_id or task["server_id"] not in {None, "", task_id}
                or any(not isinstance(payload.get(k), str) or not payload[k].strip() for k in ("feed_id", "content"))
                or canonical_payload(task["tool"], payload) != task["fingerprint"]):
            raise ValueError("ERR_RECEIPT_MISMATCH: queued comment identity changed")
        # Go mutationSignature marshals struct{Payload any; Assets []string}.
        # encoding/json escapes HTML and the two JavaScript line separators.
        signature = json.dumps({"Payload": [payload["feed_id"], payload["content"]], "Assets": []},
                               ensure_ascii=False, separators=(",", ":"))
        for character, escaped in (("&", r"\u0026"), ("<", r"\u003c"), (">", r"\u003e"),
                                   ("\u2028", r"\u2028"), ("\u2029", r"\u2029")):
            signature = signature.replace(character, escaped)
        expected = hashlib.sha256((task["tool"] + "\n" + signature).encode()).hexdigest()
        result = transport.call("get_operation_status", {"operation_id": task_id})
        if (not isinstance(result, dict) or result.get("id") != task_id or result.get("tool") != task["tool"]
                or result.get("fingerprint") != expected):
            raise ValueError("ERR_RECEIPT_MISMATCH: Go receipt does not identify this exact comment")
        stamp = result.get("updated_at")
        try:
            if not isinstance(stamp, str) or not re.search(r"(?:Z|[+-][0-9]{2}:[0-9]{2})$", stamp):
                raise ValueError()
            parse_timestamp(stamp)
        except ValueError:
            raise ValueError("ERR_RECEIPT_MISMATCH: dated Go receipt required") from None
        status = result.get("status")
        if repeated_import and status != task["status"]:
            raise ValueError("ERR_RECEIPT_MISMATCH: terminal task and current Go receipt disagree")
        if status not in {"succeeded", "confirmed", "unknown"}:
            return {"id": task_id, "status": "queued", "receipt_status": status,
                    "action_required": "回执未确认成功；保留手动任务，不发送或恢复自动队列"}
        if status in {"succeeded", "confirmed"} and (
                not isinstance(result.get("result"), dict) or result["result"].get("feed_id") != payload["feed_id"]
                or result["result"].get("success") is not True):
            raise ValueError("ERR_RECEIPT_MISMATCH: successful receipt must include success=true and the exact target")
        if repeated_import:
            return {"id": task_id, "status": status, "receipt_imported": True, "duplicate": True}
        with store.transaction():
            changed = store.db.execute("UPDATE tasks SET status=?,attempts=1,error_code=?,server_id=?,result=?,completed_at=?,handling_evidence=? WHERE id=? AND status='queued' AND tool='post_comment_to_feed' AND attempts=0 AND auto_run=0 AND owner IS ? AND lease_until IS ? AND server_id IS ? AND account=? AND fingerprint=? AND payload=? AND approval_ref=? AND run_id=? AND handling_evidence IS ? AND EXISTS (SELECT 1 FROM ops_runs WHERE id=tasks.run_id)",
                (status, "ERR_OPERATION_UNKNOWN" if status == "unknown" else None, task_id,
                 json.dumps(result, ensure_ascii=False), store.now() if status in {"succeeded", "confirmed"} else 0,
                 imported_marker, task_id, task["owner"], task["lease_until"], task["server_id"], task["account"], task["fingerprint"], task["payload"],
                 task["approval_ref"], task["run_id"], task["handling_evidence"])).rowcount
            if changed != 1:
                raise ValueError("ERR_RECEIPT_TASK_CHANGED: preserve receipt and inspect current task")
            store.event(task_id, "reconciled")
        return {"id": task_id, "status": status, "receipt_imported": True}
    if task["status"] != "unknown":
        raise ValueError("only unknown tasks require reconcile")
    result = transport.call("get_operation_status", {"operation_id": task["server_id"] or task_id})
    status = result.get("status")
    if status not in {"succeeded", "confirmed", "not_applied"}:
        return {"id": task_id, "status": "unknown", "action_required": "核对平台结果；不允许自动重发"}
    with store.transaction():
        new_status = ("queued" if task["attempts"] < store.config.max_attempts else "blocked") if status == "not_applied" else status
        store.db.execute("UPDATE tasks SET status=?,error_code=NULL,next_at=?,result=?,completed_at=? WHERE id=? AND status='unknown'", (new_status, store.now(), json.dumps(result, ensure_ascii=False), store.now() if new_status in {"succeeded", "confirmed"} else 0, task_id))
        store.event(task_id, "reconciled")
    return {"id": task_id, "status": new_status}


def runtime_snapshot(config):
    request = urllib.request.Request(config.url.rsplit("/mcp", 1)[0] + "/runtime")
    if config.token:
        request.add_header("Authorization", "Bearer " + config.token)
    with urllib.request.urlopen(request, timeout=3) as response:
        data = json.load(response)["data"]
    if data.get("account") != config.account or data.get("state_id") != config.state_id or data.get("version") != VERSION:
        raise ValueError("ERR_SERVICE_IDENTITY: runtime binding mismatch")
    return data


def recover_pending(store, transport):
    """Only consult durable records/local runtime; never navigate through a verification wall."""
    now = store.now()
    for task in store.db.execute("SELECT * FROM tasks WHERE status='unknown' AND reconcile_after<=? ORDER BY rowid LIMIT 1", (now,)).fetchall():
        store.db.execute("UPDATE tasks SET reconcile_after=? WHERE id=?", (now + 60, task["id"]))
        try:
            reconcile(store, transport, task["id"])
        except ToolFailure:
            pass  # not found is not proof of no submission; leave unknown, no replay.
    blocked = store.db.execute("SELECT * FROM tasks WHERE status='blocked' AND error_code IN ('ERR_BROWSER_UNAVAILABLE','ERR_BROWSER_NOT_CONNECTED','ERR_MCP_NOT_DISPATCHED','ERR_NOT_LOGGED_IN','ERR_LOGIN_STATE_UNKNOWN','ERR_CAPTCHA') AND recovery_count<?", (store.config.recovery_limit,)).fetchall()
    if not blocked:
        return
    try:
        data = runtime_snapshot(store.config)
    except (OSError, ValueError, KeyError):
        return
    candidate = next((t for t in blocked if t["error_code"] in {"ERR_BROWSER_UNAVAILABLE", "ERR_BROWSER_NOT_CONNECTED", "ERR_LOGIN_STATE_UNKNOWN"} and t["probe_count"] < store.config.recovery_limit and now >= t["reconcile_after"]), None)
    if candidate and not data.get("captcha_active"):
        # At most two ordinary identity checks per blockage, only after CDP is reachable.
        try:
            with urllib.request.urlopen(store.config.data["cdp_url"].rstrip("/") + "/json/version", timeout=2) as response:
                reachable = bool(json.load(response).get("webSocketDebuggerUrl"))
            if reachable:
                store.db.execute("UPDATE tasks SET probe_count=probe_count+1,reconcile_after=? WHERE id=?", (now + 60, candidate["id"]))
                transport.call("check_login_status", {"fresh": True})
                data = runtime_snapshot(store.config)
        except (OSError, ValueError, KeyError, ToolFailure):
            pass
    identity = data.get("identity", {})
    expected = store.config.data.get("expected_user_id", "")
    for task in blocked:
        # A fresh matching identity must have been verified after the blockage.
        if not expected or identity.get("user_id") != expected or not identity.get("fresh") or identity.get("checked_at_unix", 0) <= task["blocked_at"] or data.get("captcha_active"):
            continue
        with store.transaction():
            store.db.execute("UPDATE tasks SET status='queued',error_code=NULL,next_at=?,recovery_count=recovery_count+1,attempts=0 WHERE id=? AND status='blocked'", (now, task["id"]))
            store.event(task["id"], "auto_resumed", task["error_code"])
    remaining = store.db.execute("SELECT 1 FROM tasks WHERE status='blocked' AND error_code IN ('ERR_BROWSER_UNAVAILABLE','ERR_NOT_LOGGED_IN','ERR_LOGIN_STATE_UNKNOWN','ERR_CAPTCHA') LIMIT 1").fetchone()
    reason = store.db.execute("SELECT value FROM meta WHERE key='account_blocked'").fetchone()
    if not remaining and reason and reason[0] in {"ERR_BROWSER_UNAVAILABLE", "ERR_NOT_LOGGED_IN", "ERR_LOGIN_STATE_UNKNOWN", "ERR_CAPTCHA"}:
        store.db.execute("DELETE FROM meta WHERE key='account_blocked'")


def bridge(config, request):
    action = request.get("action")
    if action == "status" and not (config.state_dir / "tasks.sqlite3").exists():
        # Ordinary diagnostics must not initialize a task store or its batch
        # modules. An absent queue has no worker or maintenance to schedule.
        return {"version": VERSION, "account": config.account, "status": "not_initialized", "queue_available": False,
                "queue": {"available": False, "status": "not_initialized"}, "runner_enabled": False,
                "counts": {}, "pending_tasks": [], "next_at": None, "runner_next_at": None, "background_next_at": None}
    store = Store(config)
    try:
        store.verify_state()
        if action == "prepare_direct":
            return prepare_direct(store, request)
        if action == "complete_direct":
            return complete_direct(store, request)
        if action == "submit":
            tool = request["tool"]
            if tool in WRITES and not config.allow_write:
                raise ValueError("ERR_WRITE_NOT_ALLOWED: writes are disabled")
            if tool in WRITES and not request.get("approval_ref", "").strip() and not request.get("run_id"):
                raise ValueError("ERR_APPROVAL_REQUIRED: enable writes and provide the actual authorized scope before enqueue")
            value = store.enqueue(tool, request.get("payload", {}), request.get("approval_ref", ""), request.get("not_before", 0), request.get("dependency") or None, request.get("run_id") or None)
            if request.get("auto_run", True):
                store.db.execute("UPDATE tasks SET auto_run=1 WHERE id=?", (value["id"],))
                store.db.execute("INSERT OR REPLACE INTO meta VALUES ('runner_enabled','1')")
            return {**value, "runner_enabled": store.status()["runner_enabled"]}
        if action == "task":
            return store.task_view(request["task_id"], request.get("include_result", False))
        if action == "operation_run":
            return store.continuous.dispatch(request["operation"], request.get("data", {}))
        if action == "reconcile":
            return reconcile(store, MCPTransport(config), request["task_id"])
        if action == "resume":
            store.resume(request["task_id"], request.get("evidence", ""), request.get("approval_ref", ""))
            return store.task_view(request["task_id"])
        if action == "control":
            store.db.execute("INSERT OR REPLACE INTO meta VALUES ('runner_enabled',?)", ("1" if request.get("enabled") is True else "0",))
        if action == "advance":
            last = store.db.execute("SELECT value FROM meta WHERE key='last_ops_maintenance'").fetchone()
            if not last or store.now() - float(last[0]) >= 3600:
                store.continuous.dispatch("maintain", {})
            enabled = store.db.execute("SELECT value FROM meta WHERE key='runner_enabled'").fetchone()
            if enabled and enabled[0] == "1":
                if store.resources()["admission_allowed"]:
                    recover_pending(store, MCPTransport(config, timeout=30))
                tick(store, MCPTransport(config), automatic=True)
        return store.status()
    finally:
        store.db.close()


def verify_runtime_binding(config):
    """Core identity check only; do not open or initialize the task database."""
    record = config.state_dir / "runtime-state.json"
    if not config.state_id or not record.is_file():
        raise ValueError("ERR_STATE_UNCONFIRMED: 先 init-state；丢失状态时恢复备份，不能重建")
    data = json.loads(record.read_text())
    if data.get("state_id") != config.state_id or data.get("account") != config.account:
        raise ValueError("ERR_STATE_INCOMPATIBLE: MCP 状态和队列账号不匹配")


def queue_bootstrap_flags(config):
    """Inspect existing state read-only. No pending queue means no eager runner."""
    flags = {"XHS_OPS_QUEUE_PENDING": "0", "XHS_OPS_STATE_MAINTENANCE": "0"}
    path = config.state_dir / "tasks.sqlite3"
    if not path.is_file() or not path.stat().st_size:
        return flags
    try:
        with contextlib.closing(sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=1)) as db:
            meta = dict(db.execute("SELECT key,value FROM meta WHERE key IN ('account','state_id','runner_enabled')"))
            if meta.get("account") != config.account or meta.get("state_id") != config.state_id:
                return flags
            flags["XHS_OPS_STATE_MAINTENANCE"] = "1"
            if meta.get("runner_enabled") == "1" and db.execute("SELECT 1 FROM tasks WHERE auto_run=1 AND status IN ('queued','running','blocked','unknown') LIMIT 1").fetchone():
                flags["XHS_OPS_QUEUE_PENDING"] = "1"
    except sqlite3.Error:
        # A task-store problem must not disable ordinary read tools. The write
        # bridge will report its precise storage error before any site action.
        return {"XHS_OPS_QUEUE_PENDING": "0", "XHS_OPS_STATE_MAINTENANCE": "0"}
    return flags


def existing_queue_running(config):
    path = config.state_dir / "tasks.sqlite3"
    if not path.is_file():
        return False
    with contextlib.closing(sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=1)) as db:
        return db.execute("SELECT 1 FROM tasks WHERE status='running' LIMIT 1").fetchone() is not None


def backend_ready(config):
    """核对实际服务身份，不把任意占用该端口的服务当成本账号。"""
    request = urllib.request.Request(config.url.rsplit("/mcp", 1)[0] + "/runtime")
    if config.token:
        request.add_header("Authorization", "Bearer " + config.token)
    try:
        with urllib.request.urlopen(request, timeout=1) as response:
            data = json.load(response).get("data", {})
    except urllib.error.HTTPError as error:
        raise ValueError("ERR_SERVICE_IDENTITY: runtime endpoint rejected; check configured service/auth") from error
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return False
    except (ValueError, AttributeError):
        raise ValueError("ERR_SERVICE_IDENTITY: unexpected runtime response")
    if data.get("account") != config.account or data.get("state_id") != config.state_id or data.get("version") != VERSION:
        raise ValueError("ERR_SERVICE_IDENTITY: account/state/version/write mode mismatch; existing service preserved")
    if data.get("persistence_error"):
        raise ValueError("ERR_STATE_WRITE: service reports persistence failure")
    if data.get("config_fingerprint") != config.fingerprint() or data.get("write_enabled") != config.allow_write:
        raise ValueError("ERR_CONFIG_STALE: effective service settings differ from private config")
    return True


def managed_status(config):
    pidfile = config.state_dir / "managed-process.json"
    if not pidfile.exists():
        return {"status": "not_managed"}
    record = json.loads(pidfile.read_text())
    if record.get("config") != str(config.path):
        raise ValueError("ERR_SERVICE_IDENTITY: process record belongs to another config")
    with (config.state_dir / "supervisor.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "managed"}
    # 锁已释放仍须确认两个旧 PID 都消失；不向复用的 PID 发信号。
    for key in ("pid", "child_pid"):
        pid = int(record.get(key, 0))
        if pid <= 1:
            raise ValueError("ERR_SERVICE_IDENTITY: invalid process record")
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        raise ValueError("ERR_SERVICE_IDENTITY: unlocked record has a live PID; preserved for inspection")
    return {"status": "stale"}


def service_command(config, action):
    config.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if action in {"_serve", "foreground"}:
        return _service_command(config, action)
    # 并发 Codex 客户端只允许一个启动/停止事务，等待有限时。
    with (config.state_dir / "service-control.lock").open("a") as control:
        until = time.monotonic() + 12
        while True:
            try:
                fcntl.flock(control, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= until:
                    raise ValueError("ERR_SERVICE_BUSY: another service control operation is running")
                time.sleep(0.05)
        if action == "ensure":
            try:
                if backend_ready(config):
                    return {"status": "ready", "config_fingerprint": config.fingerprint()}
            except ValueError as error:
                if not str(error).startswith("ERR_CONFIG_STALE"):
                    raise
                if managed_status(config)["status"] != "managed":
                    raise ValueError("ERR_CONFIG_STALE: host must reload its own service") from error
                owner = json.loads((config.state_dir / "managed-process.json").read_text())
                if owner.get("runtime_sha256") != hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest():
                    raise ValueError("ERR_CONFIG_STALE: use the verified owning runtime to stop this version before upgrading")
                with (config.state_dir / "execution.lock").open("a") as execution:
                    try:
                        fcntl.flock(execution, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        raise ValueError("ERR_CONFIG_PENDING_RELOAD: task executing; settings will apply on next idle ensure")
                    if existing_queue_running(config):
                        raise ValueError("ERR_CONFIG_PENDING_RELOAD: preserve running task and its lease")
                    drain = urllib.request.Request(config.url.rsplit("/mcp", 1)[0] + "/runtime/drain", data=b"{}", headers={"Content-Type": "application/json"})
                    if config.token:
                        drain.add_header("Authorization", "Bearer " + config.token)
                    try:
                        with urllib.request.urlopen(drain, timeout=2) as response:
                            json.load(response)
                    except urllib.error.HTTPError as err:
                        raise ValueError("ERR_CONFIG_PENDING_RELOAD: active request preserved; retry ensure when idle") from err
                    _service_command(config, "stop")
                    result = _service_command(config, "start")
                    return {**result, "status": "reloaded", "config_fingerprint": config.fingerprint()}
            action = "start"
        result = _service_command(config, action)
        return {**result, "config_fingerprint": config.fingerprint()}


def _service_command(config, action):
    """只管理自己创建并持锁的 MCP 进程；永不 pkill，永不停止 Chrome。"""
    config.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    pidfile = config.state_dir / "managed-process.json"
    if action == "export-legacy":
        if managed_status(config)["status"] == "managed":
            raise ValueError("ERR_SERVICE_BUSY: stop this service before exporting rollback state")
        with (config.state_dir / "execution.lock").open("a") as execution:
            try:
                fcntl.flock(execution, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("ERR_SERVICE_BUSY: executor active; wait before rollback export")
            store = Store(config)
            try:
                store.verify_state()
                if store.db.execute("SELECT 1 FROM tasks WHERE status='running'").fetchone():
                    raise ValueError("ERR_SERVICE_BUSY: resolve the running lease before rollback export")
                env = os.environ.copy(); env.update(config.runtime_env())
                subprocess.run([str(config.resolve(config.data["mcp_binary"])), "-export-legacy-state"], env=env, capture_output=True, check=True, timeout=30)
                store.continuous.export_legacy_calendar()
            finally:
                store.db.close()
        return {"status": "legacy_exported", "note": "current receipts and deadlines preserved; switch to the matching prior runtime before restarting"}
    if action in {"_serve", "foreground"}:
        import socket
        with (config.state_dir / "supervisor.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            verify_runtime_binding(config)
            env = os.environ.copy()
            env.update(config.runtime_env())
            env.update(queue_bootstrap_flags(config))
            env.update({"XHS_CONFIG_FINGERPRINT": config.fingerprint(), "XHS_OPS_CONFIG": str(config.path), "XHS_OPS_SCRIPT": str(Path(__file__).resolve()), "XHS_OPS_PYTHON": sys.executable})
            env.pop("XHS_WRITE_INTERVAL", None)
            env.pop("XHS_LAUNCH_BROWSER", None)
            binary = config.resolve(config.data["mcp_binary"])
            logger = logging.getLogger("xhs-mcp-private"); logger.setLevel(logging.INFO)
            handler = RotatingFileHandler(config.state_dir / "mcp.log", maxBytes=4*1024*1024, backupCount=3, encoding="utf-8"); logger.addHandler(handler)
            child = subprocess.Popen([str(binary), "-headless=false", "-port", config.data.get("listen", "127.0.0.1:18061")], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            def stop_child(*_):
                child.terminate()
            signal.signal(signal.SIGTERM, stop_child)
            signal.signal(signal.SIGINT, stop_child)
            script = Path(__file__).resolve()
            config_argument = sys.argv[sys.argv.index("--config")+1] if "--config" in sys.argv else str(config.path)
            atomic_file(pidfile, json.dumps({"pid": os.getpid(), "child_pid": child.pid, "config": str(config.path),
                "runtime_script": str(script), "script_argument": sys.argv[0], "config_argument": config_argument,
                "runtime_sha256": hashlib.sha256(script.read_bytes()).hexdigest()}).encode())
            try:
                for raw in bounded_log_lines(child.stdout):
                    line = raw.decode("utf-8", "replace").rstrip()
                    line = re.sub(r"(?i)(xsec_token|web_session|auth_token|authorization|phone|code)=([^ &]+)", r"\1=[REDACTED]", line)
                    line = re.sub(r"(?i)Bearer [A-Za-z0-9._-]+", "Bearer [REDACTED]", line)
                    logger.info(line)
                return {"exit_code": child.wait()}
            finally:
                handler.close(); logger.removeHandler(handler)
                pidfile.unlink(missing_ok=True)
    if action == "start":
        if urllib.parse.urlparse(config.url).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("ERR_REMOTE_SERVICE_MANAGED_BY_HOST: will not launch a local replacement")
        verify_runtime_binding(config)
        binary = config.resolve(config.data["mcp_binary"])
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ValueError("build/install the matching MCP binary first")
        status = managed_status(config)["status"]
        if status == "managed":
            if backend_ready(config):
                return {"status": "ready"}
            raise ValueError("ERR_SERVICE_NOT_READY: existing supervisor preserved")
        if status == "stale":
            pidfile.unlink()  # 只清死进程记录；账号、队列、提交记录均保留。
        # This contains startup failures only; the service log rotates live.
        log = config.state_dir / "supervisor.log"
        if log.exists() and log.stat().st_size >= 1024 * 1024:
            handler = RotatingFileHandler(log, maxBytes=1024*1024, backupCount=2)
            try: handler.doRollover()
            finally: handler.close()
        with log.open("ab") as output:
            p = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--config", str(config.path), "service", "_serve"], stdout=output, stderr=output, start_new_session=True)
            _MANAGED[p.pid] = p
        deadline = time.monotonic() + 5
        try:
            while time.monotonic() < deadline and p.poll() is None:
                if pidfile.exists() and backend_ready(config):
                    return {"status": "started", "pid": p.pid, "note": "服务身份及就绪已核对；浏览器与登录分别检查"}
                time.sleep(0.1)
        except Exception:
            p.terminate()
            p.wait(timeout=8)
            _MANAGED.pop(p.pid, None)
            raise
        if p.poll() is None:
            p.terminate()
        p.wait(timeout=8)
        _MANAGED.pop(p.pid, None)
        raise ValueError("ERR_SERVICE_STARTUP: readiness not confirmed; inspect private mcp.log")
    if action == "status":
        return managed_status(config)
    if action == "stop":
        if not pidfile.exists():
            return {"status": "not_managed"}
        if managed_status(config)["status"] == "stale":
            pidfile.unlink()
            return {"status": "stale_record_removed", "browser": "untouched"}
        record = json.loads(pidfile.read_text())
        pid = int(record["pid"])
        # 只在 supervisor 锁被占用且进程命令匹配时发送信号，防 PID 复用。
        with (config.state_dir / "supervisor.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                command = subprocess.check_output(["ps", "-ww", "-p", str(pid), "-o", "command="], text=True)
                script = record.get("runtime_script", str(Path(__file__).resolve()))
                expected_hash = record.get("runtime_sha256")
                if expected_hash and expected_hash != hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest():
                    raise ValueError("runtime version mismatch; stop with the verified owning runtime")
                script_argument = record.get("script_argument", script)
                config_argument = record.get("config_argument", str(config.path))
                if script_argument not in command or not any(mode in command for mode in ("_serve", "foreground")) or config_argument not in command:
                    raise ValueError("PID identity mismatch; no process was stopped")
                os.kill(pid, signal.SIGTERM)
                deadline = time.monotonic() + 8
                while pidfile.exists() and time.monotonic() < deadline:
                    time.sleep(0.1)
                if pidfile.exists():
                    raise ValueError("shutdown not confirmed; preserve record and inspect process")
                managed = _MANAGED.pop(pid, None)
                if managed is not None: managed.wait(timeout=2)
                return {"status": "stopped", "browser": "untouched"}
            raise ValueError("stale process record; verify process identity before removing this private record")


def health(config):
    result = {"version": VERSION, "write_enabled": config.allow_write, "mcp": "unverified", "cdp": "unverified"}
    for field, url in (("mcp", config.url.rsplit("/mcp", 1)[0] + "/health"), ("cdp", config.data["cdp_url"].rstrip("/") + "/json/version")):
        try:
            with urllib.request.urlopen(url, timeout=min(config.timeout, 5)) as r:
                value = json.load(r)
                result[field] = "reachable" if value else "invalid"
        except Exception:
            result[field] = "unavailable"
    result["logged_in"] = "not_checked"
    return result


def prepare_state(path):
    config = Config(path)
    if config.path.name.endswith(".example.json"):
        raise ValueError("先复制示例为私有配置，再初始化状态")
    if config.state_id:
        store = Store(config)
        try: store.verify_state()
        finally: store.db.close()
        return {"status": "verified", "state_id": config.state_id}
    config.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    record = config.state_dir / "runtime-state.json"
    if record.exists():
        data = json.loads(record.read_text())
        if data.get("account") != config.account or not data.get("state_id"):
            raise ValueError("现有状态未匹配，禁止覆盖")
        state_id = data["state_id"]
    else:
        state_id = uuid.uuid4().hex
        data = {"version": 1, "state_id": state_id, "account": config.account, "operations": {}}
        atomic_file(record, json.dumps(data, ensure_ascii=False).encode())
    store = Store(config)
    store.db.execute("INSERT OR REPLACE INTO meta VALUES ('state_id',?)", (state_id,))
    store.db.close()
    config.data["expected_state_id"] = state_id
    atomic_file(config.path, (json.dumps(config.data, ensure_ascii=False, indent=2)+"\n").encode())
    return {"status": "initialized", "state_id": state_id, "note": "状态标识不用于登录或访问认证；持久磁盘保证仍需宿主验证"}


def atomic_file(path, data):
    import tempfile
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".state-")
    try:
        with os.fdopen(fd, "wb") as f:
            os.chmod(temporary, 0o600); f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("enqueue")
    p.add_argument("--tool", required=True)
    p.add_argument("--payload", required=True, help="JSON file")
    p.add_argument("--approval-ref", default="")
    p.add_argument("--not-before", type=float, default=0)
    p.add_argument("--dependency")
    commands.add_parser("init-state")
    commands.add_parser("tick")
    commands.add_parser("status")
    commands.add_parser("health")
    commands.add_parser("bridge", help="private JSON stdin bridge for MCP task tools")
    p = commands.add_parser("operation-run", help="private batch lifecycle JSON input; never logs in or creates a schedule")
    p.add_argument("operation", choices=["configure", "begin", "update", "cancel", "schedule", "review", "finish", "accept", "maintain", "status", "repair"])
    p.add_argument("--data", help="private JSON input file")
    p = commands.add_parser("reconcile"); p.add_argument("id")
    p = commands.add_parser("resume"); p.add_argument("id"); p.add_argument("--evidence", required=True); p.add_argument("--approval-ref", default="")
    p = commands.add_parser("service"); p.add_argument("action", choices=["start", "ensure", "stop", "status", "export-legacy", "foreground", "_serve"])
    args = parser.parse_args()
    try:
        config = Config(args.config)
        if args.command == "init-state":
            value = prepare_state(args.config)
        elif args.command == "service":
            value = service_command(config, args.action)
        elif args.command == "health":
            value = health(config)
        elif args.command == "status":
            value = bridge(config, {"action": "status"})
        elif args.command == "bridge":
            raw = sys.stdin.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError("task request too large")
            value = bridge(config, json.loads(raw))
        elif args.command == "operation-run":
            value = bridge(config, {"action": "operation_run", "operation": args.operation, "data": json.loads(Path(args.data).read_text()) if args.data else {}})
        else:
            store = Store(config)
            if args.command == "enqueue":
                value = store.enqueue(args.tool, json.loads(Path(args.payload).read_text()), args.approval_ref, args.not_before, args.dependency)
            elif args.command == "tick":
                value = tick(store, MCPTransport(config))
            elif args.command == "reconcile":
                value = reconcile(store, MCPTransport(config), args.id)
            else:
                store.resume(args.id, args.evidence, args.approval_ref); value = {"id": args.id, "status": "queued"}
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return int(value.get("exit_code", 0))
    except (ValueError, KeyError, OSError, ToolFailure, sqlite3.Error) as e:
        # 只输出错误码与配置说明；不输出请求或认证头。
        print(json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
