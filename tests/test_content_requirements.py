"""Offline rejection checks: no real account, face, media or publishing."""
import hashlib
import json
from pathlib import Path
import unittest

import content_fixture
import test_content_review as existing


class ContentRequirementTests(unittest.TestCase):
    setUp = existing.ContentReviewTests.setUp
    tearDown = existing.ContentReviewTests.tearDown
    begin = existing.ContentReviewTests.begin
    payloads = existing.ContentReviewTests.payloads
    comment = existing.ContentReviewTests.comment
    review_registered = existing.ContentReviewTests.review_registered

    def configure(self):
        p = content_fixture.profile(self.base)
        self.material = self.base / "full-original.txt"
        self.material.write_text("SYNTHETIC complete original material, all features and offer, no live claims")
        self.face = self.base / "face.png"
        self.face.write_bytes(b"SYNTHETIC reference; not a real person")
        p.update(product_materials=[{"file": str(self.material), "sha256": self.sha(self.material), "source_ref": "synthetic raw source"}],
                 cover_person_enabled=True,
                 face_reference={"file": str(self.face), "sha256": self.sha(self.face), "source_ref": "synthetic photo"},
                 content_requirements={"post_cta": "SYNTHETIC join group\ncredits + free trial\nfollow daily list",
                                       "post_cta_components": {"group": "join discussion", "benefit": "credits and trial", "follow": "daily jobs"},
                                       "comment_positioning_terms": ["one-stop", "preparation"],
                                       "comment_offer_phrases": ["SYNTHETIC message for free trial"], "min_person_covers": 1})
        for k in ("product_materials", "cover_person_enabled", "face_reference", "content_requirements"):
            p["confirmation_sources"][k] = "SYNTHETIC TEST ONLY"
        self.p = p
        self.assertEqual(self.c.dispatch("configure", {"content_profile": p})["content_profile_pending"], [])

    @staticmethod
    def sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def posts(self, run, batch, kinds, cta=True, same_style=False, cta_text=None, cta_review=None):
        slots = self.c.run(run["id"])["body"]["schedule_slots"]
        if not slots:
            self.payloads(run)
            slots = self.c.run(run["id"])["body"]["schedule_slots"]
        items = []
        for i, kind in enumerate(kinds):
            asset = self.c.directory(run["id"]) / "final" / (batch+str(i)+".png")
            asset.write_bytes(("SYNTHETIC cover "+batch+str(i)).encode())
            items.append({"content_id": batch+str(i), "tool": "publish_content", "payload": {
                "title": "SYNTHETIC "+batch+str(i), "content": "SYNTHETIC useful body" +
                ("\n"+(cta_text or self.p["content_requirements"]["post_cta"]) if cta else ""), "images": [str(asset)], "schedule_at": slots[i]}})
        def transform(item, fields, final):
            index = items.index(item)
            # The separate field still claims a complete CTA in the negative case.
            fields["cta"] = cta_text or self.p["content_requirements"]["post_cta"]
            if cta_review is not None:
                final["cta_review"] = cta_review
            final["cover_review"] = {"batch_id": batch, "batch_content_ids": [p["content_id"] for p in items],
                "kind": kinds[index], "asset": item["payload"]["images"][0],
                "asset_sha256": self.sha(item["payload"]["images"][0]),
                "style": "synthetic-"+str(0 if same_style else index),
                "selection_reason": "SYNTHETIC test selection", "observation": "SYNTHETIC no visual claim",
                "reference_file": str(self.face), "reference_sha256": self.sha(self.face),
                "likeness_observation": "SYNTHETIC no real face comparison"}
        content_fixture.register(self.c, run, items, transform=transform)
        return items

    def test_pass_claim_and_cta_field_cannot_replace_actual_body_cta(self):
        self.configure()
        run = self.begin(posts=1, comments=0)
        items = self.posts(run, "missing", ["person"], cta=False)
        with self.assertRaisesRegex(ValueError, "ERR_POST_CTA_REQUIRED"):
            self.review_registered(run, items)

    def test_missing_offer_rejected_and_complete_promotion_accepted(self):
        self.configure()
        run = self.begin(posts=0, comments=2)
        item = self.comment()
        content_fixture.register(self.c, run, [item], "B")
        with self.assertRaisesRegex(ValueError, "ERR_COMMENT_OFFER_REQUIRED"):
            self.review_registered(run, [item])
        item["payload"]["content"] = "SYNTHETIC app one-stop preparation; tailored questions and practice; SYNTHETIC message for free trial"
        def transform(item, fields, final):
            final["promotion_review"] = {"positioning_excerpt": "app one-stop preparation",
                "capabilities_excerpt": "tailored questions and practice", "offer_excerpt": "SYNTHETIC message for free trial",
                "need_fit": "SYNTHETIC response to the specific preparation question"}
        content_fixture.register(self.c, run, [item], "B", transform)
        self.assertEqual(self.review_registered(run, [item])["reviewed_payloads"], 1)

    def test_pure_help_does_not_require_product_or_offer(self):
        self.configure()
        run = self.begin(posts=0, comments=1)
        item = self.comment()
        content_fixture.register(self.c, run, [item], "A")
        self.assertEqual(self.review_registered(run, [item])["reviewed_payloads"], 1)

    def test_generic_capability_plus_offer_still_lacks_product_positioning(self):
        self.configure()
        run = self.begin(posts=0, comments=2)
        item = self.comment()
        item["payload"]["content"] = "SYNTHETIC prepares questions. SYNTHETIC message for free trial"
        content_fixture.register(self.c, run, [item], "B")
        with self.assertRaisesRegex(ValueError, "ERR_PRODUCT_POSITIONING_REQUIRED"):
            self.review_registered(run, [item])

    def test_reworded_cta_preserves_all_intents_without_exact_boilerplate(self):
        self.configure()
        run = self.begin(posts=1, comments=0)
        parts = {"group": "Join our discussion", "benefit": "Enjoy credits and trial access", "follow": "Follow for daily jobs"}
        reviews = {k: {"excerpt": v, "meaning_check": "SYNTHETIC equivalent intent: "+k} for k, v in parts.items()}
        items = self.posts(run, "reworded", ["person"], cta_text="\n".join(parts.values()), cta_review=reviews)
        self.assertEqual(self.review_registered(run, items)["reviewed_payloads"], 1)

    def test_reworded_cta_missing_intent_or_invented_excerpt_is_rejected(self):
        self.configure()
        run = self.begin(posts=1, comments=0)
        parts = {"group": "Join our discussion", "benefit": "Enjoy credits and trial access", "follow": "Follow for daily jobs"}
        reviews = {k: {"excerpt": v, "meaning_check": "SYNTHETIC equivalent intent"} for k, v in parts.items()}
        reviews["benefit"]["excerpt"] = "not actually in the CTA"
        items = self.posts(run, "invented", ["person"], cta_text="\n".join(parts.values()), cta_review=reviews)
        with self.assertRaisesRegex(ValueError, "ERR_POST_CTA_REQUIRED"):
            self.review_registered(run, items)

    def test_natural_promotion_variant_can_use_evidence_without_literal_phrases(self):
        self.configure()
        run = self.begin(posts=0, comments=2)
        item = self.comment()
        item["payload"]["content"] = "SYNTHETIC complete interview toolkit; tailored questions and practice; DM me to try at no cost"
        def transform(item, fields, final):
            final["promotion_review"] = {"positioning_excerpt": "complete interview toolkit",
                "capabilities_excerpt": "tailored questions and practice", "offer_excerpt": "DM me to try at no cost",
                "meaning_checks": {"positioning": "SYNTHETIC whole preparation process", "offer": "SYNTHETIC private contact and free trial"},
                "need_fit": "SYNTHETIC fits the actual interview preparation need"}
        content_fixture.register(self.c, run, [item], "B", transform)
        self.assertEqual(self.review_registered(run, [item])["reviewed_payloads"], 1)

    def test_old_batch_person_cannot_satisfy_new_batch(self):
        self.configure()
        run = self.begin(posts=2, comments=0)
        self.review_registered(run, self.posts(run, "old", ["person"]))
        with self.assertRaisesRegex(ValueError, "ERR_PERSON_COVER_REQUIRED"):
            self.review_registered(run, self.posts(run, "new", ["illustrated"]))

    def test_partial_batch_rejected_and_complete_mixed_batch_accepted(self):
        self.configure()
        run = self.begin(posts=2, comments=0)
        items = self.posts(run, "mixed", ["person", "illustrated"])
        with self.assertRaisesRegex(ValueError, "ERR_COVER_BATCH_INCOMPLETE"):
            self.review_registered(run, items[:1])
        self.assertEqual(self.review_registered(run, items)["reviewed_payloads"], 2)
        # Editing the person cover also invalidates the other post's group check.
        Path(items[0]["payload"]["images"][0]).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "ERR_CONTENT_REVIEW_STALE"):
            self.c.content.authorize(self.c.run(run["id"]), items[1]["tool"], items[1]["payload"])

    def test_same_preset_rejected(self):
        self.configure()
        run = self.begin(posts=2, comments=0)
        with self.assertRaisesRegex(ValueError, "ERR_BATCH_STYLE_DUPLICATE"):
            self.review_registered(run, self.posts(run, "same", ["person", "illustrated"], same_style=True))

    def test_changed_complete_product_source_requires_reconfiguration(self):
        self.configure()
        self.material.write_text("changed raw source")
        with self.assertRaisesRegex(ValueError, "ERR_CONTENT_PROFILE_CONFIRMATION_REQUIRED: product_materials"):
            self.c.content.profile()

    def test_person_requirement_cannot_silently_disable_photo_permission(self):
        self.configure()
        self.p["cover_person_enabled"] = False
        result = self.c.dispatch("configure", {"content_profile": self.p})
        self.assertIn("content_requirements", result["content_profile_pending"])

    def test_normal_status_can_explicitly_read_complete_private_profile(self):
        self.configure()
        before = self.s.db.execute("SELECT count(*) FROM ops_profile_versions").fetchone()[0]
        self.assertNotIn("content_profile", self.c.dispatch("status", {}))
        result = self.c.dispatch("status", {"include_profile": True})
        self.assertEqual(result["content_profile"], self.p)
        self.assertEqual(result["content_profile"]["product_materials"][0]["file"], str(self.material))
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM ops_profile_versions").fetchone()[0], before)


if __name__ == "__main__":
    unittest.main()
