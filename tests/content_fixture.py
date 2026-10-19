"""Explicit synthetic content evidence for temporary offline lifecycle tests.

This is not an authoring path and must never be used as live review evidence.
"""
import json
import hashlib
from pathlib import Path
import uuid

ROOT = Path(__file__).resolve().parents[1]


def profile(base):
    evidence = Path(base) / "synthetic-capability.txt"
    evidence.write_text("SYNTHETIC TEST ONLY: fixture product capability")
    result = {
        "persona": "synthetic test author", "target_audience": "synthetic audience",
        "objective": "offline contract verification only",
        "verified_selling_points": [{"id": "fixture-point", "claim": "synthetic capability",
                                     "evidence_ref": "synthetic fixture", "evidence_file": str(evidence)}],
        "offer": {"description": "no offer", "evidence_ref": "synthetic fixture"},
        "keywords": ["synthetic"], "boundaries": ["never publish this fixture"], "allow_c": False,
        "strategy": {"A": 40, "B": 40, "C_max": 20},
        "voice_samples": [{"text": "synthetic voice fixture", "source_ref": "synthetic fixture"}],
    }
    result["confirmation_sources"] = {k: "SYNTHETIC TEST ONLY: not human authorization" for k in result}
    return result


def register(continuous, run, items, category="A", transform=None):
    """Register separate fixture stages through the public local update path."""
    root = continuous.directory(run["id"])
    for item in items:
        content_id = item.setdefault("content_id", uuid.uuid4().hex)
        directory = root / "work" / (content_id + "-" + uuid.uuid4().hex)
        directory.mkdir()
        kind = "video" if item["tool"] == "publish_with_video" else "post" if item["tool"] == "publish_content" else "comment"
        snapshot = directory / "source-snapshot.json"
        snapshot.write_text(json.dumps({"fixture": "SYNTHETIC body, images, comments, research", "target": item["payload"].get("feed_id")}))
        source = {"kind": kind, "need": "synthetic specific need",
                  "sources": [{"ref": "SYNTHETIC TEST ONLY", "snapshot": str(snapshot),
                               "coverage": ["body", "images", "comments", "research"], "observed_at": "2026-10-16T12:00:00+08:00"}]}
        if kind == "comment":
            source["target"] = {"feed_id": item["payload"]["feed_id"]}
        fields = {k: item["payload"][k] for k in (("title", "content") if kind in {"post", "video"} else ("content",))}
        if kind == "post":
            fields.update(cover_text="synthetic image text", pages=[{"page": i, "text": "synthetic image text"}
                          for i, _ in enumerate(item["payload"].get("images", []), 1)], interaction="", cta="")
        elif kind == "video":
            fields.update(video_texts=[{"kind": "subtitle", "text": "SYNTHETIC video subtitle"}], interaction="", cta="")
        draft = {"fields": fields, "intent": "synthetic contract verification", "humanizer_rules": str(ROOT / "skills/humanizer-zh/SKILL.md")}
        edited = {"fields": fields, "edit_notes": ["SYNTHETIC unchanged fixture; tests file binding, not prose quality"]}
        final = {"fields": fields, "payload": item["payload"], "business_review": {
            **{k: "SYNTHETIC TEST ONLY: contract field" for k in ("reviewer", "source_fit", "voice", "facts", "cta")},
            "field_checks": {k: "SYNTHETIC TEST ONLY: " + k for k in fields}, "result": "pass"}}
        if kind == "comment":
            final.update(comment_type=category, need=source["need"], selling_point_ids=[] if category == "A" else ["fixture-point"])
            if category == "C":
                final["demand_evidence"] = "SYNTHETIC TEST ONLY: explicit demand"
        elif kind == "video":
            final["video_review"] = {
                "asset": item["payload"]["video"],
                "asset_sha256": hashlib.sha256(Path(item["payload"]["video"]).read_bytes()).hexdigest(),
                "video_texts": fields["video_texts"],
                "visual_observation": "SYNTHETIC TEST ONLY: not a visual assertion",
                "text_observation": "SYNTHETIC TEST ONLY: not a subtitle assertion"}
        else:
            final["image_reviews"] = [{"page": i, "asset": asset, "visible_text": "synthetic image text",
                                        "observation": "SYNTHETIC TEST ONLY: not a visual assertion"}
                                       for i, asset in enumerate(item["payload"].get("images", []), 1)]
        if transform:
            transform(item, fields, final)
        for stage, value in (("source", source), ("draft", draft), ("edited", edited), ("final", final)):
            artifact = directory / (stage + ".json")
            artifact.write_text(json.dumps(value, ensure_ascii=False))
            continuous.dispatch("update", {"run_id": run["id"], "content_record": {
                "id": content_id, "stage": stage, "artifact": str(artifact)}})
    return items
