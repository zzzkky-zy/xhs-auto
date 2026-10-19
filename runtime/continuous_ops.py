"""Private batch lifecycle. No model, scheduler, login or platform requests."""
import datetime as dt
import contextlib
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import uuid

WRITES = {"post_comment_to_feed", "reply_comment_in_feed", "publish_content", "publish_with_video"}
POSTS = {"publish_content", "publish_with_video"}
TERMINAL = {"succeeded", "confirmed", "failed", "cancelled"}


class ContinuousOps:
    def __init__(self, store):
        self.s, self.db, self.c = store, store.db, store.config
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS ops_policy (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ops_runs (id TEXT PRIMARY KEY, state TEXT NOT NULL, created REAL NOT NULL,
          updated REAL NOT NULL, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ops_repairs (id TEXT PRIMARY KEY, at REAL NOT NULL, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ops_calendar (run_id TEXT NOT NULL, at REAL NOT NULL UNIQUE);
        """)
        with self.s.transaction():
            columns = {r[1] for r in self.db.execute("PRAGMA table_info(ops_calendar)")}
            for name in ("schedule_at", "task_id"):
                if name not in columns:
                    self.db.execute("ALTER TABLE ops_calendar ADD COLUMN " + name + " TEXT")
            if "task_id" not in columns:
                # Import the old batch copy once; the calendar is the sole schedule
                # authority from here on. Preserve the original timezone spelling.
                for row in self.db.execute("SELECT id,body FROM ops_runs WHERE body LIKE '%schedule_slots%'").fetchall():
                    body = json.loads(row["body"])
                    for value in body.pop("schedule_slots", []):
                        self.db.execute("UPDATE ops_calendar SET schedule_at=? WHERE run_id=? AND at=? AND schedule_at IS NULL",
                                        (value, row["id"], dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()))
                    self.db.execute("UPDATE ops_runs SET body=? WHERE id=?", (json.dumps(body, ensure_ascii=False), row["id"]))
                for row in self.db.execute("SELECT id,payload,run_id FROM tasks WHERE run_id IS NOT NULL AND tool IN ('publish_content','publish_with_video') AND status!='cancelled'"):
                    value = json.loads(row["payload"]).get("schedule_at")
                    if value:
                        self.db.execute("UPDATE ops_calendar SET task_id=? WHERE run_id=? AND schedule_at=? AND task_id IS NULL", (row["id"], row["run_id"], value))
        from content_review import ContentReview
        self.content = ContentReview(self)

    @contextlib.contextmanager
    def lock(self):
        import fcntl
        with (self.c.state_dir / "operation-runs.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def policy(self):
        row = self.db.execute("SELECT body FROM ops_policy WHERE id=1").fetchone()
        return json.loads(row[0]) if row else None

    def save_policy(self, policy):
        self.db.execute("INSERT OR REPLACE INTO ops_policy VALUES (1,?)", (json.dumps(policy, ensure_ascii=False),))

    def run(self, run_id):
        row = self.db.execute("SELECT * FROM ops_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise ValueError("ERR_RUN_NOT_FOUND")
        body = json.loads(row["body"])
        body["schedule_slots"] = [r[0] or dt.datetime.fromtimestamp(r[1], dt.timezone.utc).isoformat()
                                  for r in self.db.execute("SELECT schedule_at,at FROM ops_calendar WHERE run_id=? ORDER BY at", (run_id,))]
        return {**dict(row), "body": body}

    def save(self, run):
        body = {k: v for k, v in run["body"].items() if k != "schedule_slots"}
        self.db.execute("UPDATE ops_runs SET state=?,updated=?,body=? WHERE id=?",
                        (run["state"], self.s.now(), json.dumps(body, ensure_ascii=False), run["id"]))

    def root(self):
        return self.c.output_dir / "operation-runs"

    def directory(self, run_id):
        if len(run_id) != 32 or any(c not in "0123456789abcdef" for c in run_id):
            raise ValueError("ERR_RUN_ID")
        root = self.root()
        if root.is_symlink():
            raise ValueError("ERR_RUN_PATH: linked root is not managed")
        return root / run_id

    def check_owner(self, run_id):
        path = self.directory(run_id)
        if path.is_symlink():
            raise ValueError("ERR_RUN_PATH: linked batch is not managed")
        expected = {"run_id": run_id, "account": self.c.account, "state_id": self.c.state_id}
        if json.loads((path / ".owner.json").read_text()) != expected:
            raise ValueError("ERR_RUN_PATH: ownership mismatch")
        return path

    def usage(self):
        total = 0
        root = self.root()
        if root.is_symlink():
            raise ValueError("ERR_RUN_PATH")
        if root.exists():
            for p in root.rglob("*"):
                if not p.is_symlink() and p.is_file():
                    total += p.stat().st_size
        return total

    def status(self):
        p = self.policy()
        version, _, pending = self.content.profile(required=False)
        row = self.db.execute("SELECT id,state,updated FROM ops_runs WHERE state NOT IN ('completed','abandoned') ORDER BY created DESC LIMIT 1").fetchone()
        return {"mode": p["mode"] if p else "not_configured", "active_run": dict(row) if row else None,
                "acceptance_run": p.get("acceptance_run") if p else None,
                "content_profile_version": version, "content_profile_pending": pending,
                "managed_disk_limit_mb": self.c.data.get("retention", {}).get("managed_disk_limit_mb", 256),
                "host_schedule": "provided_by_user_and_agent; not_created_by_this_tool"}

    def configure(self, data):
        if not self.c.data.get("expected_user_id"):
            raise ValueError("ERR_ACCOUNT_UNCONFIRMED")
        previous = self.policy()
        merged = {**(previous or {}), **data}
        for key in ("authorization_ref", "quality_standard"):
            if not isinstance(merged.get(key), str) or not merged[key].strip():
                raise ValueError("ERR_SETUP_REQUIRED: " + key)
        tools = merged.get("allowed_tools")
        if not isinstance(tools, list) or not tools or set(tools) - WRITES:
            raise ValueError("ERR_SETUP_REQUIRED: allowed_tools")
        if previous and previous["account_user_id"] != self.c.data["expected_user_id"]:
            raise ValueError("ERR_ACCOUNT_UNCONFIRMED")
        if self.status()["active_run"] and previous:
            if set(previous["allowed_tools"]) - set(tools):
                raise ValueError("ERR_RUN_ACTIVE: preserve tools needed by existing tasks")
            if set(tools) - set(previous["allowed_tools"]) and merged["authorization_ref"] == previous["authorization_ref"]:
                raise ValueError("ERR_AUTHORIZATION_REQUIRED: added tools need the new user's explicit authorization reference")
        p = {**(previous or {}), "mode": previous["mode"] if previous else "validation_required",
             "authorization_ref": merged["authorization_ref"], "quality_standard": merged["quality_standard"],
             "allowed_tools": sorted(set(tools)), "account_user_id": self.c.data["expected_user_id"]}
        if previous and (p["allowed_tools"] != previous["allowed_tools"]
                         or p["authorization_ref"] != previous["authorization_ref"]):
            p["mode"] = "validation_required"
            p.pop("acceptance_run", None)
            p.pop("human_acceptance_ref", None)
        if "content_profile" in data:
            version, _ = self.content.save_profile(data["content_profile"])
            p["content_profile_version"] = version
        # Invalidate prospective authorization only. Task identities, receipts,
        # unknown submissions and the prior acceptance decision are untouched.
        if p != previous:
            for row in self.db.execute("SELECT id FROM ops_runs WHERE state='active'").fetchall():
                run = self.run(row["id"])
                run["body"]["review"] = None
                self.save(run)
        self.save_policy(p)
        return self.status()

    def begin(self, data):
        p = self.policy()
        if not p:
            raise ValueError("ERR_SETUP_REQUIRED")
        if p["account_user_id"] != self.c.data.get("expected_user_id"):
            raise ValueError("ERR_ACCOUNT_UNCONFIRMED")
        existing = self.status()["active_run"]
        if existing:
            return {"duplicate": True, **self.run(existing["id"])}
        if not data.get("plan_ref") or not isinstance(data.get("plan"), dict):
            raise ValueError("ERR_PLAN_REQUIRED: use the current user/agent agreement")
        if len(json.dumps(data).encode()) > 32768:
            raise ValueError("ERR_PLAN_TOO_LARGE")
        self.maintain()
        self.s.require_resources()
        if self.usage() >= self.c.data.get("retention", {}).get("managed_disk_limit_mb", 256) * 1024**2:
            raise ValueError("ERR_RESOURCE_BUDGET: preserve unresolved evidence; do not start another batch")
        run_id = uuid.uuid4().hex
        path = self.directory(run_id)
        path.mkdir(parents=True, mode=0o700)
        (path / ".owner.json").write_text(json.dumps({"run_id": run_id, "account": self.c.account, "state_id": self.c.state_id}))
        (path / ".owner.json").chmod(0o600)
        for name in ("work", "final", "sealed"):
            (path / name).mkdir(mode=0o700)
        body = {"kind": "autonomous" if p["mode"] == "autonomous" else "validation",
                "plan_ref": data["plan_ref"], "plan": data["plan"], "items": {},
                "fresh_search_each_batch": True, "review": None}
        self.db.execute("INSERT INTO ops_runs VALUES (?,?,?,?,?)", (run_id, "active", self.s.now(), self.s.now(), json.dumps(body, ensure_ascii=False)))
        return {"duplicate": False, "directory": str(path), **self.run(run_id)}

    def update(self, data):
        run = self.run(data["run_id"])
        if run["state"] != "active":
            raise ValueError("ERR_RUN_CLOSED")
        if isinstance(data.get("plan"), dict) and data.get("plan_ref"):
            if len(json.dumps(data["plan"]).encode()) > 32768:
                raise ValueError("ERR_PLAN_TOO_LARGE")
            run["body"].update(plan=data["plan"], plan_ref=data["plan_ref"])
            run["body"]["review"] = None
            self.save(run)
            return run
        if isinstance(data.get("content_record"), dict):
            return self.content.record(run, data["content_record"])
        item = data.get("item")
        if not isinstance(item, dict) or not item.get("id") or item.get("stage") not in {"research", "draft", "assets", "review", "ready", "published", "skipped"}:
            raise ValueError("ERR_ITEM_REQUIRED")
        if len(json.dumps(item).encode()) > 2048 or len(run["body"]["items"]) >= 1000 and item["id"] not in run["body"]["items"]:
            raise ValueError("ERR_ITEM_TOO_LARGE")
        run["body"]["items"][item["id"]] = item
        if item["stage"] not in {"published", "skipped"}:
            run["body"]["review"] = None
        self.save(run)
        return run

    def cancel(self, data):
        with self.s.transaction():
            changed = self.db.execute("UPDATE tasks SET status='cancelled',completed_at=? WHERE id=? AND run_id=? AND status='queued'", (self.s.now(), data["task_id"], data["run_id"])).rowcount
            if changed != 1:
                raise ValueError("ERR_CANCEL_NOT_QUEUED: no cancellation applied; inspect actual task status")
            self.s.event(data["task_id"], "cancelled")
            self.db.execute("DELETE FROM ops_calendar WHERE task_id=?", (data["task_id"],))
        return {"task_id": data["task_id"], "status": "cancelled"}

    def schedule(self, data):
        run = self.run(data["run_id"])
        if run["state"] != "active":
            raise ValueError("ERR_RUN_CLOSED")
        slots = run["body"]["schedule_slots"]
        replace = data.get("replace_unsubmitted") is True
        if slots and not replace:
            return {"schedule_slots": slots, "reused": True}
        count = data.get("count")
        if type(count) is not int or not 1 <= count <= 100:
            raise ValueError("ERR_SCHEDULE_COUNT")
        first = dt.datetime.fromisoformat(data["first_at"].replace("Z", "+00:00"))
        if first.tzinfo is None or not self.s.now() + 3600 <= first.timestamp() <= self.s.now() + 14*86400:
            raise ValueError("ERR_SCHEDULE_WINDOW")
        slots = []
        at = first
        for i in range(count):
            if i:
                at += dt.timedelta(seconds=3600 + secrets.randbelow(1801))
            if at.timestamp() > self.s.now() + 14*86400:
                raise ValueError("ERR_SCHEDULE_WINDOW")
            slots.append(at.isoformat())
        with self.s.transaction():
            self.db.execute("DELETE FROM ops_calendar WHERE at<?", (self.s.now()-86400,))
            # A submitted/queued item owns its slot. Cancelling a queued item
            # releases only that item; successful siblings never prevent editing
            # the rest of the plan.
            if replace:
                self.db.execute("DELETE FROM ops_calendar WHERE run_id=? AND (task_id IS NULL OR task_id IN (SELECT id FROM tasks WHERE status IN ('cancelled','failed')))", (run["id"],))
            for value in slots:
                at = dt.datetime.fromisoformat(value).timestamp()
                if self.db.execute("SELECT 1 FROM ops_calendar WHERE at>? AND at<?", (at-3600, at+3600)).fetchone():
                    raise ValueError("ERR_SCHEDULE_CONFLICT: preserve already reserved publications")
            self.db.executemany("INSERT INTO ops_calendar(run_id,at,schedule_at) VALUES (?,?,?)", [(run["id"], dt.datetime.fromisoformat(value).timestamp(), value) for value in slots])
            run["body"]["review"] = None
            self.save(run)
        return {"schedule_slots": self.run(run["id"])["body"]["schedule_slots"], "new_slots": slots, "reused": False}

    def bind_slot(self, run_id, tool, payload, task_id):
        """Called inside enqueue's SQLite transaction, before the task is visible."""
        if tool not in POSTS:
            return
        changed = self.db.execute("UPDATE ops_calendar SET task_id=? WHERE run_id=? AND schedule_at=? AND task_id IS NULL",
                                  (task_id, run_id, payload.get("schedule_at"))).rowcount
        if changed != 1:
            raise ValueError("ERR_SCHEDULE_ALREADY_USED: every publication needs its own reserved slot")

    def export_legacy_calendar(self):
        """Offline rollback only. Older code uses a two-column INSERT."""
        with self.s.transaction():
            for row in self.db.execute("SELECT id,body FROM ops_runs").fetchall():
                body = json.loads(row["body"])
                body["schedule_slots"] = self.run(row["id"])["body"]["schedule_slots"]
                self.db.execute("UPDATE ops_runs SET body=? WHERE id=?", (json.dumps(body, ensure_ascii=False), row["id"]))
            self.db.execute("ALTER TABLE ops_calendar RENAME TO ops_calendar_current")
            self.db.execute("CREATE TABLE ops_calendar (run_id TEXT NOT NULL, at REAL NOT NULL UNIQUE)")
            self.db.execute("INSERT INTO ops_calendar SELECT run_id,at FROM ops_calendar_current")
            self.db.execute("DROP TABLE ops_calendar_current")

    def review(self, data):
        run = self.run(data["run_id"])
        if run["state"] != "active":
            raise ValueError("ERR_RUN_CLOSED")
        payloads = data.get("payloads", [])
        if not payloads or len(payloads) > 1000:
            raise ValueError("ERR_REVIEW_PAYLOADS_REQUIRED")
        p = self.policy()
        for item in payloads:
            tool, payload = item["tool"], item["payload"]
            if tool not in p["allowed_tools"]:
                raise ValueError("ERR_POLICY_SCOPE")
            if tool in POSTS and payload.get("schedule_at") not in run["body"]["schedule_slots"]:
                raise ValueError("ERR_SCHEDULE_REQUIRED: reserve the batch's persistent slots first")
        run["body"]["review"] = self.content.review(run, payloads, data.get("evidence"))
        self.save(run)
        return {"run_id": run["id"], "reviewed_payloads": len(run["body"]["review"]["fingerprints"]),
                "content_profile_version": run["body"]["review"]["profile_version"],
                "human_acceptance_required": run["body"]["kind"] == "validation",
                "evidence_boundary": run["body"]["review"]["evidence_boundary"]}

    def authorize(self, run_id, tool, payload, approval_ref):
        run = self.run(run_id)
        if run["state"] != "active":
            raise ValueError("ERR_RUN_CLOSED")
        if tool not in WRITES:
            if tool == "search_feeds":
                payload["fresh"] = True
            return payload, approval_ref
        p, review = self.policy(), run["body"].get("review")
        if p["account_user_id"] != self.c.data.get("expected_user_id") or tool not in p["allowed_tools"]:
            raise ValueError("ERR_POLICY_SCOPE")
        self.content.authorize(run, tool, payload)
        if not review or self.s.fingerprint(tool, payload) not in review["fingerprints"]:
            raise ValueError("ERR_QUALITY_REVIEW_REQUIRED: changed copy/assets require agent review")
        if run["body"]["kind"] == "validation":
            if not approval_ref:
                raise ValueError("ERR_APPROVAL_REQUIRED: first live batch needs actual authorization")
        elif p["mode"] == "autonomous":
            approval_ref = "continuous:" + p["acceptance_run"]
        else:
            raise ValueError("ERR_ACCEPTANCE_REQUIRED")
        if self.usage() >= self.c.data.get("retention", {}).get("managed_disk_limit_mb", 256) * 1024**2:
            raise ValueError("ERR_RESOURCE_BUDGET")
        # Copy local approved bytes once. Never publish from a mutable draft path.
        payload = dict(payload)
        for field in ("images", "video"):
            if field not in payload:
                continue
            values = payload[field] if isinstance(payload[field], list) else [payload[field]]
            fixed = []
            for value in values:
                if value.startswith(("http://", "https://")):
                    raise ValueError("ERR_FIXED_ASSET_REQUIRED: save approved media on the execution host")
                src = Path(value)
                if src.stat().st_size > 64*1024*1024:
                    raise ValueError("ERR_ASSET_TOO_LARGE: use a smaller approved media file")
                digest = hashlib.sha256()
                with src.open("rb") as f:
                    for chunk in iter(lambda: f.read(1024*1024), b""):
                        digest.update(chunk)
                target = self.check_owner(run_id) / "sealed" / (digest.hexdigest() + src.suffix)
                if not target.exists():
                    limit = self.c.data.get("retention", {}).get("managed_disk_limit_mb", 256)*1024**2
                    if self.usage() + src.stat().st_size > limit:
                        raise ValueError("ERR_RESOURCE_BUDGET")
                    self.s.require_resources(src.stat().st_size)
                    shutil.copyfile(src, target)
                    target.chmod(0o400)
                fixed.append(str(target))
            payload[field] = fixed if isinstance(payload[field], list) else fixed[0]
        if self.s.fingerprint(tool, payload) not in review["fingerprints"]:
            raise ValueError("ERR_ASSET_CHANGED: review the actual fixed bytes before enqueue")
        return payload, approval_ref

    def finish(self, data):
        run = self.run(data["run_id"])
        with self.s.transaction():
            self.s.settle_dependencies()
        tasks = self.db.execute("SELECT id,tool,status FROM tasks WHERE run_id=?", (run["id"],)).fetchall()
        if any(t["status"] not in TERMINAL for t in tasks):
            raise ValueError("ERR_RUN_UNRESOLVED: retain active/blocked/unknown tasks and evidence")
        if data.get("abandon") is True:
            run["state"] = "abandoned"
        else:
            if not tasks or not run["body"].get("review"):
                raise ValueError("ERR_RUN_INCOMPLETE")
            run["state"] = "awaiting_acceptance" if run["body"]["kind"] == "validation" else "completed"
        self.db.execute("DELETE FROM ops_calendar WHERE run_id=? AND (task_id IS NULL OR task_id IN (SELECT id FROM tasks WHERE status IN ('cancelled','failed')))", (run["id"],))
        run["body"]["receipt"] = [dict(t) for t in tasks]
        summary = {status: sum(t["status"] == status for t in tasks) for status in sorted(TERMINAL)}
        run["body"]["outcomes"] = summary
        self.save(run)
        cleanup = self.maintain()
        return {"run_id": run["id"], "state": run["state"], "outcomes": summary, "cleanup": cleanup}

    def accept(self, data):
        run, p = self.run(data["run_id"]), self.policy()
        if run["state"] != "awaiting_acceptance" or not data.get("human_acceptance_ref"):
            raise ValueError("ERR_HUMAN_ACCEPTANCE_REQUIRED")
        done = {r[0] for r in self.db.execute("SELECT tool FROM tasks WHERE run_id=? AND status IN ('succeeded','confirmed')", (run["id"],))}
        for family in (POSTS, WRITES - POSTS):
            if set(p["allowed_tools"]) & family and not done & family:
                raise ValueError("ERR_VALIDATION_INCOMPLETE: test every enabled workflow before acceptance")
        p.update(mode="autonomous", acceptance_run=run["id"], human_acceptance_ref=data["human_acceptance_ref"])
        self.save_policy(p)
        run["state"] = "completed"
        self.save(run)
        return {**self.status(), "cleanup": self.maintain()}

    def maintain(self):
        removed = 0
        keep_hours = self.c.data.get("retention", {}).get("abandoned_keep_hours", 72)
        for row in self.db.execute("SELECT id,state,updated,body FROM ops_runs WHERE state IN ('completed','abandoned')").fetchall():
            if row["state"] == "abandoned" and self.s.now() < row["updated"] + keep_hours * 3600:
                continue
            if self.db.execute("SELECT 1 FROM tasks WHERE run_id=? AND status NOT IN ('succeeded','confirmed','failed','cancelled')", (row["id"],)).fetchone():
                continue
            path = self.directory(row["id"])
            if path.exists() or path.is_symlink():
                self.check_owner(row["id"])
                shutil.rmtree(path)
                removed += 1
            # Keep tiny write tombstones for deduplication; remove private tokens,
            # full source text, image paths and oversized result blobs.
            with self.s.transaction():
                for task in self.db.execute("SELECT id,payload FROM tasks WHERE run_id=? AND tool IN ('post_comment_to_feed','reply_comment_in_feed','publish_content','publish_with_video')", (row["id"],)).fetchall():
                    payload = json.loads(task["payload"])
                    small = {k: payload[k] for k in ("feed_id", "user_id", "comment_id", "target_user_id") if k in payload}
                    self.db.execute("UPDATE tasks SET payload=?,result=? WHERE id=?", (json.dumps(small), json.dumps({"archived": True, "run_id": row["id"], "status": "see_persistent_operation_journal"}), task["id"]))
                self.db.execute("DELETE FROM tasks WHERE run_id=? AND tool NOT IN ('post_comment_to_feed','reply_comment_in_feed','publish_content','publish_with_video') AND NOT EXISTS (SELECT 1 FROM tasks d WHERE d.dependency=tasks.id)", (row["id"],))
                self.db.execute("UPDATE tasks SET payload='{}',result='{\"archived\":true}' WHERE run_id=? AND tool NOT IN ('post_comment_to_feed','reply_comment_in_feed','publish_content','publish_with_video')", (row["id"],))
                body = json.loads(row["body"])
                body = {k: body[k] for k in ("kind", "plan_ref", "receipt", "outcomes") if k in body}
                body["cleaned"] = True
                self.db.execute("UPDATE ops_runs SET body=? WHERE id=?", (json.dumps(body), row["id"]))
                # Keep small stage registration receipts, not duplicate complete
                # drafts after the batch's existing accepted cleanup boundary.
                for stage in self.db.execute("SELECT seq,body FROM ops_content_stages WHERE run_id=?", (row["id"],)).fetchall():
                    record = json.loads(stage["body"])
                    receipt = {"cleaned": True, "profile_version": record.get("profile_version"),
                               "parent": record.get("parent"), "files": record.get("files", [])}
                    self.db.execute("UPDATE ops_content_stages SET body=? WHERE seq=?", (json.dumps(receipt), stage["seq"]))
        self.db.execute("DELETE FROM ops_runs WHERE state IN ('completed','abandoned') AND id NOT IN (SELECT id FROM ops_runs ORDER BY created DESC LIMIT 50) AND body LIKE '%\"cleaned\": true%'")
        self.db.execute("DELETE FROM ops_content_stages WHERE run_id NOT IN (SELECT id FROM ops_runs)")
        history = self.s.maintain_history()
        self.db.execute("DELETE FROM ops_calendar WHERE at<?", (self.s.now()-86400,))
        self.db.execute("INSERT OR REPLACE INTO meta VALUES ('last_ops_maintenance',?)", (str(self.s.now()),))
        return {"removed_batch_directories": removed, "managed_bytes": self.usage(), "deduplication_records_preserved": True, **history}

    def repair(self, data):
        required = ("failure", "change", "verification", "before_version", "after_version", "rollback_ref", "skill_update")
        if not all(isinstance(data.get(k), str) and data[k].strip() for k in required):
            raise ValueError("ERR_REPAIR_EVIDENCE_REQUIRED")
        path = Path(data.get("verification_report", ""))
        if not path.is_file() or path.stat().st_size > 128*1024:
            raise ValueError("ERR_REPAIR_VERIFICATION_REQUIRED: run scripts/verify_engineering.py and supply its report")
        report = json.loads(path.read_text())
        root = Path(__file__).resolve().parents[1]
        capabilities = report.get("actual_local_stdio_capabilities", [])
        required_capabilities = WRITES | {"get_runtime_status", "get_operation_status", "submit_task", "get_task_status",
                                        "control_task_queue", "manage_operation_run", "reconcile_task", "resume_task"}
        if (data["before_version"] == data["after_version"] or data["after_version"] != (root / "VERSION").read_text().strip()
                or report.get("version") != data["after_version"] or report.get("status") != "passed"
                or report.get("source_dirty") is not False or not report.get("source_commit")
                or not isinstance(capabilities, list) or any(not isinstance(name, str) for name in capabilities)
                or not required_capabilities <= set(capabilities)
                or type(report.get("actual_local_stdio_tools")) is not int
                or report["actual_local_stdio_tools"] != len(set(capabilities)) or not report.get("records")
                or any(r.get("exit_code") != 0 for r in report["records"])):
            raise ValueError("ERR_REPAIR_VERIFICATION_REQUIRED: require a frozen, checked version with real local stdio acceptance")
        binary = self.c.resolve(self.c.data["mcp_binary"])
        if (report.get("binary_sha256") != hashlib.sha256(binary.read_bytes()).hexdigest()
                or report.get("runtime_sha256") != hashlib.sha256((root / "runtime/xhs_ops.py").read_bytes()).hexdigest()
                or report.get("stdio_entry_sha256") != hashlib.sha256((root / "runtime/codex_mcp.py").read_bytes()).hexdigest()):
            raise ValueError("ERR_REPAIR_VERIFICATION_REQUIRED: report does not match the actual runtime artifacts")
        actual = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in sorted((root / "runtime").glob("*.py"))}
        if report.get("runtime_files_sha256") != actual:
            raise ValueError("ERR_REPAIR_VERIFICATION_REQUIRED: runtime modules changed after verification")
        body = {k: data[k][:2000] for k in required}
        body.update(verification_report_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    source_commit=report["source_commit"], binary_sha256=report["binary_sha256"])
        repair_id = uuid.uuid4().hex
        self.db.execute("INSERT INTO ops_repairs VALUES (?,?,?)", (repair_id, self.s.now(), json.dumps(body, ensure_ascii=False)))
        self.db.execute("DELETE FROM ops_repairs WHERE id NOT IN (SELECT id FROM ops_repairs ORDER BY at DESC LIMIT 20)")
        return {"repair_id": repair_id, "verified_version": report["version"], "note": "local artifact checks recorded; production activation and site acceptance remain separate evidence"}

    def dispatch(self, action, data):
        methods = {"configure": self.configure, "begin": self.begin, "update": self.update, "cancel": self.cancel,
                   "schedule": self.schedule, "review": self.review, "finish": self.finish,
                   "accept": self.accept, "repair": self.repair}
        if action == "status":
            result = self.status()
            if data.get("include_profile") is True:
                version, profile, pending = self.content.profile(required=False)
                result.update(content_profile_version=version, content_profile=profile,
                              content_profile_pending=pending)
            return result
        if action == "maintain":
            with self.lock():
                return self.maintain()
        if action not in methods:
            raise ValueError("ERR_RUN_ACTION")
        with self.lock():
            return methods[action](data)
