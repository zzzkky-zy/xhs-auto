"""Objective content evidence binding, never a language-quality evaluator.

Registered stages are contemporaneous file snapshots, not proof that a model
actually read, generated, edited or visually inspected them. Those judgements
remain the operator's responsibility.
"""
import datetime as dt
import hashlib
import json
from pathlib import Path
import content_requirements

PROFILE_FIELDS = ("persona", "target_audience", "objective", "verified_selling_points",
                  "offer", "keywords", "boundaries", "allow_c", "strategy", "voice_samples")
STAGES = ("source", "draft", "edited", "final")
POSTS = {"publish_content", "publish_with_video"}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def text(value):
    return isinstance(value, str) and bool(value.strip())


class ContentReview:
    def __init__(self, continuous):
        self.o, self.s, self.db = continuous, continuous.s, continuous.db
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS ops_profile_versions
          (version TEXT PRIMARY KEY, created REAL NOT NULL, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ops_content_stages
          (seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, content_id TEXT NOT NULL,
           stage TEXT NOT NULL, at REAL NOT NULL, digest TEXT NOT NULL, body TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS ops_content_latest
          ON ops_content_stages(run_id,content_id,stage,seq);
        """)

    def profile_missing(self, profile):
        missing = []
        confirmations = profile.get("confirmation_sources", {})
        for key in PROFILE_FIELDS:
            value = profile.get(key)
            valid = value is not None
            if key in {"persona", "target_audience", "objective"}:
                valid = text(value)
            elif key in {"keywords", "boundaries"}:
                valid = isinstance(value, list) and (bool(value) or key == "boundaries") and all(text(v) for v in value)
            elif key == "allow_c":
                valid = type(value) is bool
            elif key == "verified_selling_points":
                valid = isinstance(value, list) and bool(value) and all(
                    isinstance(v, dict) and all(text(v.get(k)) for k in ("id", "claim", "evidence_ref", "evidence_file")) for v in value)
                if valid:
                    valid = len({v["id"] for v in value}) == len(value)
            elif key == "offer":
                valid = isinstance(value, dict) and text(value.get("description")) and text(value.get("evidence_ref"))
            elif key == "strategy":
                valid = (isinstance(value, dict) and {"A", "B", "C_max"} <= set(value) <= {"A", "B", "C_max", "fallback"}
                         and all(type(value[k]) is int and 0 <= value[k] <= 100 for k in ("A", "B", "C_max"))
                         and value["A"] > 0 and value["B"] > 0 and value["A"] + value["B"] + value["C_max"] == 100)
                fallback = value.get("fallback") if isinstance(value, dict) else None
                if fallback is not None:
                    valid = (valid and isinstance(fallback, dict) and set(fallback) == {"A", "B", "C"}
                             and all(type(v) is int and v >= 0 for v in fallback.values())
                             and fallback["A"] > 0 and fallback["B"] > 0 and fallback["C"] == 0
                             and sum(fallback.values()) == 100)
            elif key == "voice_samples":
                valid = isinstance(value, list) and bool(value) and all(
                    isinstance(v, dict) and text(v.get("text")) and text(v.get("source_ref")) for v in value)
            if not valid or not isinstance(confirmations, dict) or not text(confirmations.get(key)):
                missing.append(key)
        # Optional for legacy profiles; new onboarding records an explicit choice.
        if "cover_person_enabled" in profile:
            enabled = profile["cover_person_enabled"]
            if type(enabled) is not bool or not isinstance(confirmations, dict) or not text(confirmations.get("cover_person_enabled")):
                missing.append("cover_person_enabled")
            if enabled is True:
                face = profile.get("face_reference")
                valid = (isinstance(face, dict) and all(text(face.get(k)) for k in ("file", "sha256", "source_ref"))
                         and isinstance(confirmations, dict) and text(confirmations.get("face_reference")))
                if valid:
                    try:
                        valid = self.file(face["file"], max_bytes=64*1024*1024)["sha256"] == face["sha256"]
                    except (ValueError, OSError):
                        valid = False
                if not valid:
                    missing.append("face_reference")
        return missing + content_requirements.missing(profile, self.file)

    def save_profile(self, profile):
        if not isinstance(profile, dict) or len(json.dumps(profile).encode()) > 32768:
            raise ValueError("ERR_CONTENT_PROFILE: provide a bounded profile object")
        # The immutable version records confirmed and pending fields alike.
        version = digest(profile)
        self.db.execute("INSERT OR IGNORE INTO ops_profile_versions VALUES (?,?,?)",
                        (version, self.s.now(), json.dumps(profile, ensure_ascii=False)))
        return version, self.profile_missing(profile)

    def profile(self, required=True):
        policy = self.o.policy() or {}
        version = policy.get("content_profile_version")
        row = self.db.execute("SELECT body FROM ops_profile_versions WHERE version=?", (version,)).fetchone()
        profile = json.loads(row[0]) if row else {}
        missing = self.profile_missing(profile)
        if required and (not row or digest(profile) != version or missing):
            raise ValueError("ERR_CONTENT_PROFILE_CONFIRMATION_REQUIRED: " + ",".join(missing))
        return version, profile, missing

    def file(self, value, root=None, max_bytes=16*1024*1024):
        path = Path(value) if isinstance(value, str) else Path("")
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            raise ValueError("ERR_CONTENT_ARTIFACT: absolute regular file required")
        if root is not None:
            try:
                path.resolve().relative_to(root.resolve())
            except ValueError:
                raise ValueError("ERR_CONTENT_ARTIFACT: evidence must stay in the owned batch") from None
            if any(p.is_symlink() for p in [path, *path.parents] if p != root.parent):
                raise ValueError("ERR_CONTENT_ARTIFACT: linked evidence is not managed")
        if path.stat().st_size > max_bytes:
            raise ValueError("ERR_CONTENT_ARTIFACT: evidence file too large")
        h = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024*1024), b""):
                h.update(chunk)
        return {"path": str(path), "sha256": h.hexdigest(), "size": path.stat().st_size}

    def unchanged(self, files):
        for expected in files:
            if self.file(expected["path"], max_bytes=64*1024*1024) != expected:
                raise ValueError("ERR_CONTENT_REVIEW_STALE: a bound evidence file changed")

    def latest(self, run_id, content_id, stage):
        row = self.db.execute("SELECT * FROM ops_content_stages WHERE run_id=? AND content_id=? AND stage=? ORDER BY seq DESC LIMIT 1",
                              (run_id, content_id, stage)).fetchone()
        return {**dict(row), "body": json.loads(row["body"])} if row else None

    def chain(self, run_id, content_id, through="final"):
        result, previous = [], None
        for stage in STAGES[:STAGES.index(through)+1]:
            row = self.latest(run_id, content_id, stage)
            if not row or row["body"].get("parent") != (previous["seq"] if previous else None):
                raise ValueError("ERR_CONTENT_STAGE_REQUIRED: register current " + stage + " separately")
            if previous and row["seq"] <= previous["seq"]:
                raise ValueError("ERR_CONTENT_STAGE_REQUIRED: stage order")
            self.unchanged(row["body"]["files"])
            result.append(row)
            previous = row
        return result

    def fields(self, fields, kind):
        required = {"content"} if kind == "comment" else {"title", "content", "interaction", "cta"}
        required |= {"video_texts"} if kind == "video" else {"cover_text", "pages"} if kind == "post" else set()
        if not isinstance(fields, dict) or required - fields.keys() or not text(fields.get("content")):
            raise ValueError("ERR_CONTENT_FIELDS_REQUIRED")
        if kind == "post":
            if any(not isinstance(fields[k], str) for k in ("title", "content", "cover_text", "interaction", "cta")):
                raise ValueError("ERR_CONTENT_FIELDS_REQUIRED")
            pages = fields["pages"]
            if not isinstance(pages, list) or not pages or any(
                    not isinstance(p, dict) or p.get("page") != i or not isinstance(p.get("text"), str)
                    for i, p in enumerate(pages, 1)):
                raise ValueError("ERR_PAGE_TEXT_REQUIRED: numbered exact text for every page")
        elif kind == "video":
            if any(not isinstance(fields[k], str) for k in ("title", "content", "interaction", "cta")):
                raise ValueError("ERR_CONTENT_FIELDS_REQUIRED")
            texts = fields["video_texts"]
            if not isinstance(texts, list) or any(
                    not isinstance(item, dict) or item.get("kind") not in {"on_screen", "subtitle"}
                    or not text(item.get("text")) for item in texts):
                raise ValueError("ERR_VIDEO_TEXT_REQUIRED: exact on-screen and subtitle text list")
            if not any(item["kind"] == "subtitle" for item in texts) and not text(fields.get("subtitle_absence_reason")):
                raise ValueError("ERR_VIDEO_TEXT_REQUIRED: explain why this video has no subtitles")

    def record(self, run, record):
        content_id, stage = record.get("id"), record.get("stage")
        if not text(content_id) or len(content_id) > 128 or stage not in STAGES:
            raise ValueError("ERR_CONTENT_STAGE_REQUIRED")
        root = self.o.check_owner(run["id"])
        artifact = self.file(record.get("artifact"), root, max_bytes=256*1024)
        try:
            value = json.loads(Path(artifact["path"]).read_text())
        except (ValueError, UnicodeError):
            raise ValueError("ERR_CONTENT_ARTIFACT: stage JSON object required") from None
        if not isinstance(value, dict):
            raise ValueError("ERR_CONTENT_ARTIFACT: stage JSON object required")
        files, previous, source = [artifact], None, value
        if stage != "source":
            chain = self.chain(run["id"], content_id, STAGES[STAGES.index(stage)-1])
            previous, source = chain[-1], chain[0]["body"]["value"]
            if artifact["path"] in {f["path"] for r in chain for f in r["body"]["files"]}:
                raise ValueError("ERR_CONTENT_ARTIFACT: retain a separate file for each stage")
        kind = source.get("kind")
        if stage == "source":
            if kind not in {"comment", "post", "video"} or not text(value.get("need")):
                raise ValueError("ERR_CONTENT_SOURCE_REQUIRED")
            sources, coverage = value.get("sources"), set()
            if not isinstance(sources, list) or not sources:
                raise ValueError("ERR_CONTENT_SOURCE_REQUIRED")
            for item in sources:
                if not isinstance(item, dict) or not text(item.get("ref")) or not isinstance(item.get("coverage"), list):
                    raise ValueError("ERR_CONTENT_SOURCE_REQUIRED")
                try:
                    observed = dt.datetime.fromisoformat(item["observed_at"].replace("Z", "+00:00"))
                    if observed.tzinfo is None:
                        raise ValueError()
                except (KeyError, AttributeError, ValueError):
                    raise ValueError("ERR_CONTENT_SOURCE_REQUIRED: observed_at with timezone") from None
                files.append(self.file(item.get("snapshot"), root))
                coverage.update(item["coverage"])
            required = {"body", "images", "comments"} if kind == "comment" else {"research"}
            if required - coverage:
                raise ValueError("ERR_CONTENT_SOURCE_REQUIRED: missing coverage " + ",".join(sorted(required-coverage)))
            if kind == "comment" and (not isinstance(value.get("target"), dict) or not text(value["target"].get("feed_id"))):
                raise ValueError("ERR_CONTENT_SOURCE_REQUIRED: exact comment target")
        else:
            self.fields(value.get("fields"), kind)
            if stage == "draft":
                if not text(value.get("intent")):
                    raise ValueError("ERR_CONTENT_INTENT_REQUIRED")
                rules = self.file(value.get("humanizer_rules"), max_bytes=256*1024)
                expected = Path(__file__).resolve().parents[1] / "skills/humanizer-zh/SKILL.md"
                if Path(rules["path"]).resolve() != expected.resolve():
                    raise ValueError("ERR_HUMANIZER_RULES_REQUIRED: use this version's original Humanizer")
                files.append(rules)
            elif stage == "edited":
                notes = value.get("edit_notes")
                if not isinstance(notes, list) or not notes or not all(text(n) for n in notes):
                    raise ValueError("ERR_CONTENT_EDIT_RECORD_REQUIRED")
            else:
                if value["fields"] != previous["body"]["value"]["fields"]:
                    raise ValueError("ERR_CONTENT_FINAL_CHANGED: edit the actual changed copy first")
                if not isinstance(value.get("payload"), dict):
                    raise ValueError("ERR_CONTENT_FINAL_REQUIRED")
        version, _, _ = self.profile(required=False)
        body = {"value": value, "files": files, "parent": previous["seq"] if previous else None,
                "profile_version": version}
        stamp = digest(body)
        old = self.latest(run["id"], content_id, stage)
        if old and old["digest"] == stamp:
            return {"content_id": content_id, "stage": stage, "seq": old["seq"], "reused": True}
        cursor = self.db.execute("INSERT INTO ops_content_stages(run_id,content_id,stage,at,digest,body) VALUES (?,?,?,?,?,?)",
                                 (run["id"], content_id, stage, self.s.now(), stamp, json.dumps(body, ensure_ascii=False)))
        review = run["body"].get("review")
        if review and review.get("schema_version") == 2:
            review["entries"].pop(content_id, None)
            review["fingerprints"] = {e["fingerprint"]: e["tool"] for e in review["entries"].values()}
        else:
            run["body"]["review"] = None
        self.o.save(run)
        return {"content_id": content_id, "stage": stage, "seq": cursor.lastrowid,
                "artifact_sha256": artifact["sha256"], "profile_version": version,
                "evidence_boundary": "registered file order and bytes; not proof of semantic or visual review"}

    def profile_files(self, profile):
        files = [self.file(p["evidence_file"]) for p in profile["verified_selling_points"]]
        files.extend(self.file(p["file"]) for p in profile.get("product_materials", []))
        if profile["offer"].get("evidence_file"):
            files.append(self.file(profile["offer"]["evidence_file"]))
        return files

    def strategy(self, run, profile, entries):
        content_requirements.check_batches(self, profile, entries)
        plan = run["body"]["plan"]
        comments = [e for e in entries.values() if e["kind"] == "comment"]
        posts = [e for e in entries.values() if e["kind"] in {"post", "video"}]
        existing = self.db.execute("SELECT tool,fingerprint FROM tasks WHERE run_id=? AND status NOT IN ('cancelled','failed')", (run["id"],)).fetchall()
        for key, items in (("comments", comments), ("posts", posts)):
            limit = plan.get(key)
            fingerprints = {e["fingerprint"] for e in items}
            fingerprints.update(r["fingerprint"] for r in existing
                                if (r["tool"] in POSTS) == (key == "posts")
                                and r["tool"] in POSTS | {"post_comment_to_feed", "reply_comment_in_feed"})
            if fingerprints and (type(limit) is not int or limit < len(fingerprints)):
                raise ValueError("ERR_CONTENT_PLAN_SCOPE: " + key)
        if not comments:
            return
        strategy, total = plan.get("comment_strategy"), plan["comments"]
        if (not isinstance(strategy, dict) or set(strategy) != {"A", "B", "C"}
                or any(type(v) is not int or v < 0 for v in strategy.values()) or sum(strategy.values()) != total):
            raise ValueError("ERR_COMMENT_STRATEGY_REQUIRED: record the agreed batch counts")
        agreed = profile["strategy"]
        if strategy["C"] and (not profile["allow_c"] or strategy["C"]*100 > total*agreed["C_max"]):
            raise ValueError("ERR_COMMENT_STRATEGY_SCOPE: C not allowed or above its cap")
        # When C is unsuitable, distribute the remainder in the confirmed A:B
        # ratio. Integer rounding within one comment is the only automatic slack.
        remainder = total-strategy["C"]
        ratio = agreed.get("fallback", agreed) if not strategy["C"] else agreed
        target_a = remainder*ratio["A"]/(ratio["A"]+ratio["B"])
        if abs(strategy["A"]-target_a) > 1 or (total >= 2 and (not strategy["A"] or not strategy["B"])):
            if not text(plan.get("strategy_adjustment_ref")):
                raise ValueError("ERR_COMMENT_STRATEGY_CONFIRMATION_REQUIRED: retain the user's A/B agreement")
        counts = {k: sum(e.get("comment_type") == k for e in comments) for k in ("A", "B", "C")}
        if any(counts[k] > strategy[k] for k in counts):
            raise ValueError("ERR_COMMENT_STRATEGY_SCOPE: reviewed items exceed agreed type counts")

    def entry(self, run, item, version, profile):
        content_id = item.get("content_id")
        if not text(content_id):
            raise ValueError("ERR_CONTENT_STAGE_REQUIRED: content_id binds the actual stages")
        chain = self.chain(run["id"], content_id)
        if any(r["body"]["profile_version"] != version for r in chain[1:]):
            raise ValueError("ERR_CONTENT_REVIEW_STALE: create/edit/review with the current profile")
        source, final = chain[0]["body"]["value"], chain[-1]["body"]["value"]
        tool, payload = item.get("tool"), item.get("payload")
        kind = "video" if tool == "publish_with_video" else "post" if tool == "publish_content" else "comment"
        if source["kind"] != kind or not isinstance(payload, dict) or final["payload"] != payload:
            raise ValueError("ERR_CONTENT_FINAL_CHANGED: review the exact final payload")
        fields = final["fields"]
        content_requirements.check_copy(profile, kind, final, payload)
        for key in ({"title", "content"} if kind in {"post", "video"} else {"content"}):
            if payload.get(key) != fields.get(key):
                raise ValueError("ERR_CONTENT_FINAL_CHANGED: final fields differ from publishing copy")
        business = final.get("business_review")
        if (not isinstance(business, dict) or business.get("result") != "pass"
                or not all(text(business.get(k)) for k in ("reviewer", "source_fit", "voice", "facts", "cta"))
                or not isinstance(business.get("field_checks"), dict)
                or any(not text(business["field_checks"].get(k)) for k in fields)):
            raise ValueError("ERR_CONTENT_BUSINESS_REVIEW_REQUIRED: actual per-field review records")
        files = [f for r in chain for f in r["body"]["files"]]
        result = {"content_id": content_id, "kind": kind, "stage_seqs": [r["seq"] for r in chain], "files": files,
                  "fingerprint": self.s.fingerprint(tool, payload), "tool": tool}
        if kind == "comment":
            if any(payload.get(k) != v for k, v in source["target"].items()):
                raise ValueError("ERR_CONTENT_TARGET_CHANGED")
            category = final.get("comment_type")
            if category not in {"A", "B", "C"} or final.get("need") != source["need"]:
                raise ValueError("ERR_COMMENT_NEED_REQUIRED")
            points = final.get("selling_point_ids", [])
            if not isinstance(points, list) or set(points)-{p["id"] for p in profile["verified_selling_points"]}:
                raise ValueError("ERR_SELLING_POINT_EVIDENCE_REQUIRED")
            if category in {"B", "C"} and not points:
                raise ValueError("ERR_SELLING_POINT_EVIDENCE_REQUIRED")
            if category == "C" and (not profile["allow_c"] or not text(final.get("demand_evidence"))):
                raise ValueError("ERR_C_DEMAND_PERMISSION_REQUIRED")
            result["comment_type"] = category
        elif kind == "video":
            review = final.get("video_review")
            if (not isinstance(review, dict) or review.get("asset") != payload.get("video")
                    or review.get("video_texts") != fields["video_texts"]
                    or not all(text(review.get(k)) for k in ("visual_observation", "text_observation"))):
                raise ValueError("ERR_VIDEO_REVIEW_REQUIRED: exact asset, text list and actual observation records")
            asset = self.file(payload.get("video"), self.o.check_owner(run["id"]), max_bytes=64*1024*1024)
            if review.get("asset_sha256") != asset["sha256"]:
                raise ValueError("ERR_VIDEO_REVIEW_REQUIRED: actual video hash differs from the reviewed asset")
            files.append(asset)
        else:
            images = payload.get("images", [])
            pages, reviews = fields["pages"], final.get("image_reviews")
            if not isinstance(reviews, list) or len(images) != len(pages) or len(reviews) != len(pages):
                raise ValueError("ERR_PAGE_TEXT_REQUIRED")
            for page, review, asset in zip(pages, reviews, images):
                if (not isinstance(review, dict) or review.get("page") != page["page"] or review.get("asset") != asset
                        or review.get("visible_text") != page["text"] or not text(review.get("observation"))):
                    raise ValueError("ERR_PAGE_TEXT_REQUIRED: every image must match its exact page text")
                files.append(self.file(asset, self.o.check_owner(run["id"]), max_bytes=64*1024*1024))
            result.update(content_requirements.cover_entry(self, profile, final, payload, content_id, files))
        return result

    def review(self, run, items, evidence):
        if not text(evidence):
            raise ValueError("ERR_QUALITY_REVIEW_REQUIRED: record the actual cross-item review")
        version, profile, _ = self.profile()
        profile_files = self.profile_files(profile)
        old = run["body"].get("review") or {}
        plan_hash = digest({k: run["body"][k] for k in ("plan", "plan_ref")})
        entries = dict(old.get("entries", {})) if old.get("profile_version") == version and old.get("plan_hash") == plan_hash else {}
        # Existing entries must remain bound to current files; stale entries are
        # explicitly reworked rather than silently carried into a fresh review.
        for entry in entries.values():
            self.unchanged(entry["files"])
        for item in items:
            entry = self.entry(run, item, version, profile)
            entries[entry["content_id"]] = entry
        if len({e["fingerprint"] for e in entries.values()}) != len(entries):
            raise ValueError("ERR_CONTENT_DUPLICATE: one final payload cannot fill several strategy positions")
        self.strategy(run, profile, entries)
        return {"schema_version": 2, "profile_version": version, "profile_files": profile_files,
                "plan_hash": plan_hash, "entries": entries,
                "fingerprints": {e["fingerprint"]: e["tool"] for e in entries.values()},
                "evidence": evidence[:4000], "registered_at": self.s.now(),
                "evidence_boundary": "objective completeness and unchanged bytes; semantic review remains the agent's responsibility"}

    def authorize(self, run, tool, payload):
        review = run["body"].get("review")
        version, profile, _ = self.profile()
        if (not review or review.get("schema_version") != 2 or review.get("profile_version") != version
                or review.get("plan_hash") != digest({k: run["body"][k] for k in ("plan", "plan_ref")})):
            raise ValueError("ERR_CONTENT_REVIEW_STALE: current configuration and plan require a new review")
        self.unchanged(review["profile_files"])
        self.strategy(run, profile, review["entries"])
        fingerprint = self.s.fingerprint(tool, payload)
        entry = next((e for e in review["entries"].values() if e["fingerprint"] == fingerprint and e["tool"] == tool), None)
        if not entry:
            raise ValueError("ERR_QUALITY_REVIEW_REQUIRED: changed copy/assets require agent review")
        chain = self.chain(run["id"], entry["content_id"])
        if [r["seq"] for r in chain] != entry["stage_seqs"]:
            raise ValueError("ERR_CONTENT_REVIEW_STALE: stages changed after review")
        self.unchanged(entry["files"])
