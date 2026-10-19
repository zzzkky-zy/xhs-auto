"""Check account-confirmed copy and cover requirements against final artifacts.

These checks bind evidence; they cannot judge whether a face or prose looks good.
"""


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def compact(value):
    return "".join(value.split())


def excerpt_record(record, body):
    """A semantic judgement must point to actual copy, not a pass checkbox."""
    return (isinstance(record, dict) and nonempty(record.get("excerpt"))
            and compact(record["excerpt"]) in compact(body)
            and nonempty(record.get("meaning_check")))


def missing(profile, file_check):
    result = []
    confirmations = profile.get("confirmation_sources", {})
    for key in ("product_materials", "content_requirements"):
        if key not in profile:
            continue  # Existing accounts retain their confirmed choices.
        if not isinstance(confirmations, dict) or not nonempty(confirmations.get(key)):
            result.append(key)
    if "product_materials" in profile:
        materials = profile["product_materials"]
        valid = isinstance(materials, list) and bool(materials)
        if valid:
            for material in materials:
                try:
                    valid = (isinstance(material, dict) and nonempty(material.get("source_ref"))
                             and file_check(material.get("file"))["sha256"] == material.get("sha256"))
                except (ValueError, OSError):
                    valid = False
                if not valid:
                    break
        if not valid:
            result.append("product_materials")
    if "content_requirements" in profile:
        rules = profile["content_requirements"]
        valid = isinstance(rules, dict) and nonempty(rules.get("post_cta"))
        if valid:
            phrases = rules.get("comment_offer_phrases")
            positioning = rules.get("comment_positioning_terms")
            minimum = rules.get("min_person_covers")
            valid = (isinstance(phrases, list) and bool(phrases) and all(nonempty(p) for p in phrases)
                     and isinstance(positioning, list) and bool(positioning) and all(nonempty(p) for p in positioning)
                     and type(minimum) is int and minimum >= 0
                     and (minimum == 0 or profile.get("cover_person_enabled") is True)
                     and bool(profile.get("product_materials")))
            components = rules.get("post_cta_components")
            if components is not None:
                valid = (valid and isinstance(components, dict) and bool(components)
                         and all(nonempty(k) and nonempty(v) for k, v in components.items()))
        if not valid:
            result.append("content_requirements")
    return result


def check_copy(profile, kind, final, payload):
    rules = profile.get("content_requirements")
    if not rules:
        return
    body = payload.get("content", "")
    if kind in {"post", "video"}:
        cta = final["fields"].get("cta", "")
        if not nonempty(cta) or compact(cta) not in compact(body):
            raise ValueError("ERR_POST_CTA_REQUIRED: actual CTA must appear in the publishing body")
        if compact(cta) != compact(rules["post_cta"]):
            components, review = rules.get("post_cta_components"), final.get("cta_review", {})
            if (not components or not isinstance(review, dict)
                    or any(not excerpt_record(review.get(key), cta) for key in components)):
                raise ValueError("ERR_POST_CTA_REQUIRED: a reworded CTA needs actual excerpts for every confirmed intent")
    elif final.get("comment_type") in {"B", "C"}:
        review = final.get("promotion_review", {})
        review = review if isinstance(review, dict) else {}
        meanings = review.get("meaning_checks", {})
        meanings = meanings if isinstance(meanings, dict) else {}
        offer = review.get("offer_excerpt", "")
        positioning = review.get("positioning_excerpt", "")
        offer_equivalent = excerpt_record({"excerpt": offer, "meaning_check": meanings.get("offer")}, body)
        positioning_equivalent = excerpt_record({"excerpt": positioning, "meaning_check": meanings.get("positioning")}, body)
        if not any(phrase in body for phrase in rules["comment_offer_phrases"]) and not offer_equivalent:
            raise ValueError("ERR_COMMENT_OFFER_REQUIRED: actual promotional copy must include the confirmed offer")
        if not all(term.casefold() in body.casefold() for term in rules["comment_positioning_terms"]) and not positioning_equivalent:
            raise ValueError("ERR_PRODUCT_POSITIONING_REQUIRED: retain the account's confirmed product positioning")
        # Exact excerpts make a claimed pass inspectable, without restricting
        # every comment to one feature or requiring identical boilerplate.
        if (not isinstance(review, dict)
                or any(not nonempty(review.get(k)) or review[k] not in body
                       for k in ("positioning_excerpt", "capabilities_excerpt", "offer_excerpt"))
                or not (any(p in review["offer_excerpt"] for p in rules["comment_offer_phrases"]) or offer_equivalent)
                or not (all(t.casefold() in review["positioning_excerpt"].casefold()
                            for t in rules["comment_positioning_terms"]) or positioning_equivalent)
                or not nonempty(review.get("need_fit"))):
            raise ValueError("ERR_PROMOTION_REVIEW_REQUIRED: quote actual positioning, capabilities and offer; explain relevance")


def cover_entry(owner, profile, final, payload, content_id, files):
    if not profile.get("content_requirements", {}).get("min_person_covers", 0):
        return {}
    review = final.get("cover_review")
    if not isinstance(review, dict):
        raise ValueError("ERR_COVER_REVIEW_REQUIRED")
    members = review.get("batch_content_ids")
    if (not nonempty(review.get("batch_id")) or not isinstance(members, list) or not members
            or not all(nonempty(m) for m in members) or len(set(members)) != len(members)
            or content_id not in members or review.get("kind") not in {"person", "illustrated"}
            or any(not nonempty(review.get(k)) for k in ("style", "selection_reason", "observation"))):
        raise ValueError("ERR_COVER_REVIEW_REQUIRED: name the current delivery batch, members, style and observations")
    images = payload.get("images", [])
    if not images or review.get("asset") != images[0]:
        raise ValueError("ERR_COVER_REVIEW_REQUIRED: the reviewed cover must be the first publishing image")
    cover = owner.file(images[0], max_bytes=64*1024*1024)
    if review.get("asset_sha256") != cover["sha256"]:
        raise ValueError("ERR_COVER_REVIEW_REQUIRED: cover bytes changed")
    bound = [cover]
    if review["kind"] == "person":
        face = profile["face_reference"]
        if (review.get("reference_file") != face["file"] or review.get("reference_sha256") != face["sha256"]
                or not nonempty(review.get("likeness_observation"))):
            raise ValueError("ERR_PERSON_REFERENCE_REQUIRED: record the supplied photo and actual likeness review")
        reference = owner.file(face["file"], max_bytes=64*1024*1024)
        if reference["sha256"] != face["sha256"]:
            raise ValueError("ERR_PERSON_REFERENCE_REQUIRED: configured photo changed")
        bound.append(reference)
    files.extend(bound)
    return {"cover_review": review, "cover_files": bound}


def check_batches(owner, profile, entries):
    minimum = profile.get("content_requirements", {}).get("min_person_covers", 0)
    if not minimum:
        return
    groups = {}
    for entry in entries.values():
        if entry["kind"] != "post":
            continue
        cover = entry.get("cover_review")
        if not cover:
            raise ValueError("ERR_COVER_REVIEW_REQUIRED")
        groups.setdefault(cover["batch_id"], []).append(entry)
        owner.unchanged(entry["cover_files"])
    for group in groups.values():
        actual = {e["content_id"] for e in group}
        if any(set(e["cover_review"]["batch_content_ids"]) != actual for e in group):
            raise ValueError("ERR_COVER_BATCH_INCOMPLETE: review all declared posts in this delivery batch together")
        if sum(e["cover_review"]["kind"] == "person" for e in group) < minimum:
            raise ValueError("ERR_PERSON_COVER_REQUIRED: this delivery batch lacks its required person cover")
        if len({e["cover_review"]["style"] for e in group}) != len(group):
            raise ValueError("ERR_BATCH_STYLE_DUPLICATE: each post needs a different existing preset")
